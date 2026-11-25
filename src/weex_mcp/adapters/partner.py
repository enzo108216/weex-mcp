from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..secret_provider import SecretProvider
from ..tenant_context import TenantContext

PARTNER_BASE_URL = "https://api-spot.weex.com"
PARTNER_PRODUCTION_ENVIRONMENT = "partner_production"
PARTNER_TEST_ENVIRONMENT = "partner_test"
PARTNER_ENVIRONMENTS = frozenset({PARTNER_PRODUCTION_ENVIRONMENT, PARTNER_TEST_ENVIRONMENT})
PARTNER_TIMEOUT_SECONDS = 30.0

_LONG_MAX = 9_223_372_036_854_775_807
_HOST_LABEL_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_SAFE_UPSTREAM_CODE_PATTERN = re.compile(r"(?:-?[0-9]{1,20}|upstream_rejected)\Z")
_SAFE_RATE_BUCKET_PATTERN = re.compile(r"[A-Z0-9-]{1,32}\Z")
_SAFE_ERROR_CATEGORIES = frozenset(
    {"authentication", "permission", "rate_limit", "validation", "upstream", "transport", "schema", "redirect", "local_policy"}
)
_SAFE_TRANSPORT_REASONS = frozenset({"TimeoutError", "URLError", "OSError", "HTTPError"})
_SUCCESS_CODES = {"0", "00000", "200", "SUCCESS", "success", "OK", "ok"}
_AUTHENTICATION_CODES = {"-1040", "-1041", "-1042", "-1043", "-1044", "-1046", "-1047", "-1049"}
_PERMISSION_CODES = {"-1050", "-1051", "-1052", "-1053", "-1055", "-1056", "-1057", "-1058", "-1190"}
_VALIDATION_CODES = {"-1045", "-1128", "-1135", "-1140", "-1141", "-1142", "-1150", "-1160", "-1170", "-1171"}

PreparedTransport = Callable[[dict[str, Any], float], tuple[int, Mapping[str, Any], bytes]]


@dataclass(frozen=True)
class _PartnerEndpoint:
    key: str
    method: str
    path: str
    weight: int


_ENDPOINTS = {
    "affiliate_uids": _PartnerEndpoint(
        key="partner.get-affiliate-uids",
        method="GET",
        path="/api/v3/rebate/affiliate/getAffiliateUIDs",
        weight=20,
    ),
    "channel_trade_asset": _PartnerEndpoint(
        key="partner.get-channel-user-trade-and-asset",
        method="GET",
        path="/api/v3/rebate/affiliate/getChannelUserTradeAndAsset",
        weight=20,
    ),
    "affiliate_commission": _PartnerEndpoint(
        key="partner.get-affiliate-commission",
        method="GET",
        path="/api/v3/rebate/affiliate/getAffiliateCommission",
        weight=20,
    ),
    "sub_channel_transactions": _PartnerEndpoint(
        key="partner.query-sub-channel-transactions",
        method="POST",
        path="/api/v3/rebate/affiliate/querySubChannelTransactions",
        weight=10,
    ),
    "verify_referrals": _PartnerEndpoint(
        key="partner.verify-referrals",
        method="GET",
        path="/api/v3/agency/verifyReferrals",
        weight=10,
    ),
    "referral_assets": _PartnerEndpoint(
        key="partner.get-referral-assets",
        method="GET",
        path="/api/v3/agency/getAssert",
        weight=10,
    ),
    "referral_deal_data": _PartnerEndpoint(
        key="partner.get-referral-deal-data",
        method="GET",
        path="/api/v3/agency/getDealData",
        weight=10,
    ),
}


class PartnerAdapterError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        category: str,
        http_status: int | None = None,
        upstream_code: str | None = None,
        reason: str | None = None,
        rate_limit: dict[str, dict[str, int]] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.category = category
        self.http_status = http_status
        self.upstream_code = upstream_code
        self.reason = reason
        self.rate_limit = rate_limit

    def safe_details(self) -> dict[str, Any]:
        category = (
            self.category
            if isinstance(self.category, str) and self.category in _SAFE_ERROR_CATEGORIES
            else "upstream"
        )
        details: dict[str, Any] = {"category": category, "retry_automatic": False}
        if isinstance(self.http_status, int) and not isinstance(self.http_status, bool) and 100 <= self.http_status <= 599:
            details["http_status"] = self.http_status
        if isinstance(self.upstream_code, str) and _SAFE_UPSTREAM_CODE_PATTERN.fullmatch(self.upstream_code):
            details["upstream_code"] = self.upstream_code
        if isinstance(self.reason, str) and self.reason in _SAFE_TRANSPORT_REASONS:
            details["reason"] = self.reason
        safe_rate_limit = _safe_rate_limit_details(self.rate_limit)
        if safe_rate_limit is not None:
            details["rate_limit"] = safe_rate_limit
        return details


class PartnerAdapter(Protocol):
    def get_affiliate_uids(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        uid: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        page: str | None = None,
        page_size: str | None = None,
        expected_environment: str | None = None,
    ) -> dict[str, Any]: ...

    def get_channel_user_trade_and_asset(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        uid: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        page: str | None = None,
        page_size: str | None = None,
        expected_environment: str | None = None,
    ) -> dict[str, Any]: ...

    def get_affiliate_commission(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        uid: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        coin: str | None = None,
        product_type: str | None = None,
        page: str | None = None,
        page_size: str | None = None,
        expected_environment: str | None = None,
    ) -> dict[str, Any]: ...

    def query_sub_channel_transactions(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        product_type: str,
        start_time: str | None,
        end_time: str | None,
        sub_uid: str | None = None,
        page_num: str | None = None,
        page_size: str | None = None,
        expected_environment: str | None = None,
    ) -> dict[str, Any]: ...

    def verify_referrals(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        user_ids: list[str],
        expected_environment: str | None = None,
    ) -> dict[str, Any]: ...

    def get_referral_assets(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        user_id: str,
        start_time: str,
        end_time: str,
        expected_environment: str | None = None,
    ) -> dict[str, Any]: ...

    def get_referral_deal_data(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        start_time: str,
        end_time: str,
        user_ids: list[str] | None = None,
        expected_environment: str | None = None,
    ) -> dict[str, Any]: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())


class WeexPartnerRestAdapter:
    """Strict read-only adapter for the seven allowlisted Partner endpoints."""

    def __init__(
        self,
        *,
        secret_provider: SecretProvider,
        base_url: str = PARTNER_BASE_URL,
        timeout_seconds: float = PARTNER_TIMEOUT_SECONDS,
        locale: str = "en-US",
        transport: PreparedTransport | None = None,
        time_func: Callable[[], float] | None = None,
    ) -> None:
        self.base_url, self.environment = _classify_partner_origin(base_url)
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive number.")
        if not isinstance(locale, str) or not locale:
            raise ValueError("locale must be a non-empty string.")
        self.secret_provider = secret_provider
        self.timeout_seconds = float(timeout_seconds)
        self.locale = locale
        self.transport = transport or _default_transport
        self.time_func = time_func or time.time

    def get_affiliate_uids(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        uid: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        page: str | None = None,
        page_size: str | None = None,
        expected_environment: str | None = None,
    ) -> dict[str, Any]:
        self._require_expected_environment(expected_environment)
        query = _common_query(uid, start_time, end_time, page, page_size)
        return self._execute("affiliate_uids", query=query, body={}, credential_argument=credential_argument, context=context)

    def get_channel_user_trade_and_asset(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        uid: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        page: str | None = None,
        page_size: str | None = None,
        expected_environment: str | None = None,
    ) -> dict[str, Any]:
        self._require_expected_environment(expected_environment)
        query = _common_query(uid, start_time, end_time, page, page_size)
        return self._execute("channel_trade_asset", query=query, body={}, credential_argument=credential_argument, context=context)

    def get_affiliate_commission(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        uid: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        coin: str | None = None,
        product_type: str | None = None,
        page: str | None = None,
        page_size: str | None = None,
        expected_environment: str | None = None,
    ) -> dict[str, Any]:
        self._require_expected_environment(expected_environment)
        normalized_uid, normalized_start, normalized_end, normalized_page, normalized_size = _common_values(
            uid, start_time, end_time, page, page_size
        )
        normalized_coin = _optional_enum(coin, field="coin", allowed={"USDT", "BTC"})
        normalized_product = _optional_enum(
            product_type, field="product_type", allowed={"SPOT", "FUTURES"}
        )
        query: dict[str, Any] = {}
        _put_if_present(query, "uid", normalized_uid)
        _put_if_present(query, "startTime", normalized_start)
        _put_if_present(query, "endTime", normalized_end)
        _put_if_present(query, "coin", normalized_coin)
        _put_if_present(query, "productType", normalized_product)
        _put_if_present(query, "page", normalized_page)
        _put_if_present(query, "pageSize", normalized_size)
        return self._execute("affiliate_commission", query=query, body={}, credential_argument=credential_argument, context=context)

    def query_sub_channel_transactions(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        product_type: str,
        start_time: str | None,
        end_time: str | None,
        sub_uid: str | None = None,
        page_num: str | None = None,
        page_size: str | None = None,
        expected_environment: str | None = None,
    ) -> dict[str, Any]:
        self._require_expected_environment(expected_environment)
        normalized_product = _required_enum(
            product_type, field="product_type", allowed={"SPOT", "FUTURES"}
        )
        normalized_sub_uid = _optional_integer(sub_uid, field="sub_uid", minimum=1)
        normalized_page = _optional_integer(page_num, field="page_num", minimum=1, maximum=None)
        normalized_size = _optional_integer(page_size, field="page_size", minimum=1, maximum=100)
        if (start_time is None) != (end_time is None):
            raise ValueError("start_time and end_time must both be strings or both be null.")
        if start_time is None:
            if expected_environment != PARTNER_TEST_ENVIRONMENT or self.environment != PARTNER_TEST_ENVIRONMENT:
                raise PartnerAdapterError(
                    "partner_test_time_binding_required",
                    category="local_policy",
                )
            normalized_start: str | None = None
            normalized_end: str | None = None
        else:
            normalized_start = _integer_string(start_time, field="start_time", minimum=0)
            normalized_end = _integer_string(end_time, field="end_time", minimum=0)
            assert normalized_end is not None
            if int(normalized_start) > int(normalized_end):
                raise ValueError("start_time must be less than or equal to end_time.")
        body: dict[str, Any] = {}
        _put_if_present(body, "subUid", int(normalized_sub_uid) if normalized_sub_uid is not None else None)
        body["startTime"] = int(normalized_start) if normalized_start is not None else None
        body["endTime"] = int(normalized_end) if normalized_end is not None else None
        body["productType"] = normalized_product
        _put_if_present(body, "pageNum", int(normalized_page) if normalized_page is not None else None)
        _put_if_present(body, "pageSize", int(normalized_size) if normalized_size is not None else None)
        return self._execute("sub_channel_transactions", query={}, body=body, credential_argument=credential_argument, context=context)

    def verify_referrals(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        user_ids: list[str],
        expected_environment: str | None = None,
    ) -> dict[str, Any]:
        self._require_expected_environment(expected_environment)
        normalized_ids = _integer_list(user_ids, field="user_ids", maximum_items=100, unique=True)
        return self._execute(
            "verify_referrals",
            query={"userIds": ",".join(normalized_ids)},
            body={},
            credential_argument=credential_argument,
            context=context,
        )

    def get_referral_assets(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        user_id: str,
        start_time: str,
        end_time: str,
        expected_environment: str | None = None,
    ) -> dict[str, Any]:
        self._require_expected_environment(expected_environment)
        normalized_user_id = _integer_string(user_id, field="user_id", minimum=1)
        normalized_start, normalized_end = _date_range(start_time, end_time)
        return self._execute(
            "referral_assets",
            query={"userId": normalized_user_id, "startTime": normalized_start, "endTime": normalized_end},
            body={},
            credential_argument=credential_argument,
            context=context,
        )

    def get_referral_deal_data(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        start_time: str,
        end_time: str,
        user_ids: list[str] | None = None,
        expected_environment: str | None = None,
    ) -> dict[str, Any]:
        self._require_expected_environment(expected_environment)
        normalized_start, normalized_end = _date_range(start_time, end_time)
        query: dict[str, Any] = {}
        if user_ids is not None:
            query["userIds"] = _integer_list(user_ids, field="user_ids", unique=False)
        query["startTime"] = normalized_start
        query["endTime"] = normalized_end
        return self._execute(
            "referral_deal_data",
            query=query,
            body={},
            credential_argument=credential_argument,
            context=context,
        )

    def _require_expected_environment(self, expected_environment: str | None) -> None:
        if expected_environment is None:
            return
        if expected_environment not in PARTNER_ENVIRONMENTS:
            raise ValueError("expected_environment must be partner_production or partner_test.")
        if expected_environment != self.environment:
            raise PartnerAdapterError(
                "expected_partner_environment_mismatch",
                category="local_policy",
            )

    def _execute(
        self,
        endpoint_name: str,
        *,
        query: Mapping[str, Any],
        body: Mapping[str, Any],
        credential_argument: dict[str, Any],
        context: TenantContext,
    ) -> dict[str, Any]:
        endpoint = _ENDPOINTS[endpoint_name]
        if (endpoint.method == "GET" and body) or (endpoint.method == "POST" and query):
            raise PartnerAdapterError("partner_contract_error", category="local_policy")
        credential = self.secret_provider.resolve(
            credential_argument=credential_argument,
            context=context,
            required_permission="USER_DATA",
        )
        query_string = urlencode(query, doseq=True)
        body_string = json.dumps(body, ensure_ascii=False, separators=(",", ":")) if endpoint.method == "POST" else ""
        timestamp_ms = str(int(self.time_func() * 1000))
        signature = _sign(
            api_secret=credential.api_secret,
            timestamp_ms=timestamp_ms,
            method=endpoint.method,
            path=endpoint.path,
            query_string=query_string,
            body=body_string,
        )
        url = f"{self.base_url}{endpoint.path}"
        if query_string:
            url = f"{url}?{query_string}"
        prepared = {
            "method": endpoint.method,
            "path": endpoint.path,
            "url": url,
            "headers": {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "locale": self.locale,
                "User-Agent": "weex-mcp/0.1 partner-readonly",
                "ACCESS-KEY": credential.api_key,
                "ACCESS-PASSPHRASE": credential.api_passphrase,
                "ACCESS-TIMESTAMP": timestamp_ms,
                "ACCESS-SIGN": signature,
            },
            "data": body_string.encode("utf-8") if body_string else None,
            "query_string": query_string,
            "query": dict(query),
            "body": dict(body),
        }
        status, headers, raw = self._call_transport(prepared)
        rate_limit = _rate_limit_metadata(headers)
        if 300 <= status < 400:
            raise PartnerAdapterError(
                "partner_redirect_forbidden",
                category="redirect",
                http_status=status,
                rate_limit=rate_limit,
            )
        try:
            payload = _decode_json(raw)
        except PartnerAdapterError as exc:
            if 200 <= status < 300:
                raise PartnerAdapterError(
                    "partner_response_schema_error",
                    category="schema",
                    http_status=status,
                    rate_limit=rate_limit,
                ) from exc
            payload = None
        payload = _redact_secret_literals(
            payload,
            (credential.api_key, credential.api_secret, credential.api_passphrase),
        )
        if self.environment == PARTNER_TEST_ENVIRONMENT and _contains_origin(payload, self.base_url):
            payload = _redact_origin_content(payload, self.base_url)
            if _contains_origin(payload, self.base_url):
                raise PartnerAdapterError(
                    "partner_output_redaction_failed",
                    category="schema",
                    http_status=status,
                    rate_limit=rate_limit,
                )
        if not 200 <= status < 300:
            raise _http_or_business_error(status=status, payload=payload, rate_limit=rate_limit)
        if not isinstance(payload, (dict, list)):
            raise PartnerAdapterError(
                "partner_response_schema_error",
                category="schema",
                http_status=status,
                rate_limit=rate_limit,
            )
        business_error = _successful_http_business_error(payload, status=status, rate_limit=rate_limit)
        if business_error is not None:
            raise business_error
        return {
            "endpoint": endpoint.key,
            "method": endpoint.method,
            "path": endpoint.path,
            "weight": endpoint.weight,
            "http_status": status,
            "rate_limit": rate_limit,
            "retry": {"automatic": False},
            "environment": self.environment,
            "capability_mode": "read_only_query",
            "data": payload,
        }

    def _call_transport(self, prepared: dict[str, Any]) -> tuple[int, Mapping[str, Any], bytes]:
        try:
            response = self.transport(prepared, self.timeout_seconds)
        except HTTPError as exc:
            try:
                return int(exc.code), dict(exc.headers or {}), exc.read()
            finally:
                exc.close()
        except (TimeoutError, URLError, OSError) as exc:
            raise PartnerAdapterError(
                "partner_transport_error",
                category="transport",
                reason=exc.__class__.__name__,
            ) from exc
        except Exception as exc:
            raise PartnerAdapterError(
                "partner_transport_error",
                category="transport",
                reason=exc.__class__.__name__,
            ) from exc
        if (
            not isinstance(response, tuple)
            or len(response) != 3
            or isinstance(response[0], bool)
            or not isinstance(response[0], int)
            or not isinstance(response[1], Mapping)
            or not isinstance(response[2], bytes)
        ):
            raise PartnerAdapterError("partner_response_schema_error", category="schema")
        return response


def _classify_partner_origin(raw_url: str) -> tuple[str, str]:
    value = str(raw_url or "")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise PartnerAdapterError("partner_origin_invalid", category="local_policy") from exc
    hostname = parsed.hostname or ""
    try:
        hostname.encode("ascii")
    except UnicodeEncodeError as exc:
        raise PartnerAdapterError("partner_origin_invalid", category="local_policy") from exc
    labels = hostname.split(".")
    valid_labels = bool(labels) and all(_HOST_LABEL_PATTERN.fullmatch(label) for label in labels)
    canonical = f"https://{hostname}"
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or any(ord(character) < 33 or ord(character) == 127 for character in value)
        or parsed.scheme != "https"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not valid_labels
        or value != canonical
    ):
        raise PartnerAdapterError("partner_origin_invalid", category="local_policy")
    if canonical == PARTNER_BASE_URL:
        return canonical, PARTNER_PRODUCTION_ENVIRONMENT
    if hostname != "weex.tech" and hostname.endswith(".weex.tech"):
        return canonical, PARTNER_TEST_ENVIRONMENT
    raise PartnerAdapterError("partner_origin_invalid", category="local_policy")


def _common_values(
    uid: str | None,
    start_time: str | None,
    end_time: str | None,
    page: str | None,
    page_size: str | None,
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    normalized_uid = _optional_integer(uid, field="uid", minimum=1)
    if (start_time is None) != (end_time is None):
        raise ValueError("start_time and end_time must be provided together.")
    normalized_start = _optional_integer(start_time, field="start_time", minimum=0)
    normalized_end = _optional_integer(end_time, field="end_time", minimum=0)
    if normalized_start is not None and normalized_end is not None and int(normalized_start) > int(normalized_end):
        raise ValueError("start_time must be less than or equal to end_time.")
    normalized_page = _optional_integer(page, field="page", minimum=1, maximum=None)
    normalized_size = _optional_integer(page_size, field="page_size", minimum=1, maximum=100)
    return normalized_uid, normalized_start, normalized_end, normalized_page, normalized_size


def _common_query(
    uid: str | None,
    start_time: str | None,
    end_time: str | None,
    page: str | None,
    page_size: str | None,
) -> dict[str, Any]:
    values = _common_values(uid, start_time, end_time, page, page_size)
    query: dict[str, Any] = {}
    for wire_name, value in zip(("uid", "startTime", "endTime", "page", "pageSize"), values, strict=True):
        _put_if_present(query, wire_name, value)
    return query


def _integer_string(
    value: Any,
    *,
    field: str,
    minimum: int,
    maximum: int | None = _LONG_MAX,
) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or (len(value) > 1 and value.startswith("0")):
        raise ValueError(f"{field} must be a canonical decimal string.")
    parsed = int(value)
    if parsed < minimum or (maximum is not None and parsed > maximum):
        raise ValueError(f"{field} is outside the allowed range.")
    return value


def _optional_integer(
    value: Any,
    *,
    field: str,
    minimum: int,
    maximum: int | None = _LONG_MAX,
) -> str | None:
    if value is None:
        return None
    return _integer_string(value, field=field, minimum=minimum, maximum=maximum)


def _integer_list(
    values: Any,
    *,
    field: str,
    maximum_items: int | None = None,
    unique: bool,
) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{field} must be a non-empty array.")
    if maximum_items is not None and len(values) > maximum_items:
        raise ValueError(f"{field} contains too many items.")
    normalized = [_integer_string(value, field=field, minimum=1) for value in values]
    if unique and len(set(normalized)) != len(normalized):
        raise ValueError(f"{field} must contain unique values.")
    return normalized


def _required_enum(value: Any, *, field: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{field} is not an allowed value.")
    return value


def _optional_enum(value: Any, *, field: str, allowed: set[str]) -> str | None:
    if value is None:
        return None
    return _required_enum(value, field=field, allowed=allowed)


def _date_range(start_time: Any, end_time: Any) -> tuple[str, str]:
    start = _date_string(start_time, field="start_time")
    end = _date_string(end_time, field="end_time")
    if start > end:
        raise ValueError("start_time must be less than or equal to end_time.")
    return start, end


def _date_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError(f"{field} must use YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} must be a valid calendar date.") from None
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must use YYYY-MM-DD.")
    return value


def _put_if_present(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def _sign(
    *,
    api_secret: str,
    timestamp_ms: str,
    method: str,
    path: str,
    query_string: str,
    body: str,
) -> str:
    message = f"{timestamp_ms}{method}{path}"
    if query_string:
        message += f"?{query_string}"
    message += body
    digest = hmac.new(api_secret.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _decode_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PartnerAdapterError("partner_response_schema_error", category="schema") from exc


def _business_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for field in ("code", "errorCode"):
        if field not in payload or payload[field] is None:
            continue
        value = payload[field]
        if isinstance(value, (dict, list, tuple, set)):
            raise PartnerAdapterError("partner_response_schema_error", category="schema")
        return str(value)
    return None


def _successful_http_business_error(
    payload: Any,
    *,
    status: int,
    rate_limit: dict[str, dict[str, int]],
) -> PartnerAdapterError | None:
    try:
        code = _business_code(payload)
    except PartnerAdapterError:
        return PartnerAdapterError(
            "partner_response_schema_error",
            category="schema",
            http_status=status,
            rate_limit=rate_limit,
        )
    if isinstance(payload, dict) and payload.get("success") is False and (code is None or code in _SUCCESS_CODES):
        return PartnerAdapterError(
            "partner_upstream_error",
            category="upstream",
            http_status=status,
            upstream_code="upstream_rejected",
            rate_limit=rate_limit,
        )
    if code is None or code in _SUCCESS_CODES:
        return None
    return _http_or_business_error(status=status, payload=payload, rate_limit=rate_limit)


def _http_or_business_error(
    *,
    status: int,
    payload: Any,
    rate_limit: dict[str, dict[str, int]],
) -> PartnerAdapterError:
    if status == 429:
        return PartnerAdapterError(
            "partner_rate_limit",
            category="rate_limit",
            http_status=status,
            rate_limit=rate_limit,
        )
    try:
        code = _business_code(payload)
    except PartnerAdapterError:
        code = None
    if status == 401 or code in _AUTHENTICATION_CODES:
        category, machine_code = "authentication", "partner_authentication_error"
    elif status == 403 or code in _PERMISSION_CODES:
        category, machine_code = "permission", "partner_permission_error"
    elif status == 400 or code in _VALIDATION_CODES:
        category, machine_code = "validation", "partner_validation_error"
    else:
        category, machine_code = "upstream", "partner_upstream_error"
    safe_code = code if code and _SAFE_UPSTREAM_CODE_PATTERN.fullmatch(code) else None
    return PartnerAdapterError(
        machine_code,
        category=category,
        http_status=status,
        upstream_code=safe_code,
        rate_limit=rate_limit,
    )


def _safe_rate_limit_details(value: Any) -> dict[str, dict[str, int]] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, dict[str, int]] = {"used": {}, "remaining": {}}
    for group in ("used", "remaining"):
        entries = value.get(group)
        if not isinstance(entries, Mapping):
            continue
        for bucket, item in entries.items():
            if not isinstance(bucket, str):
                continue
            normalized_bucket = bucket.upper()
            if (
                not _SAFE_RATE_BUCKET_PATTERN.fullmatch(normalized_bucket)
                or isinstance(item, bool)
                or not isinstance(item, int)
                or item < 0
            ):
                continue
            result[group][normalized_bucket] = item
    return result


def _rate_limit_metadata(headers: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {"used": {}, "remaining": {}}
    for key, value in headers.items():
        upper = str(key).upper()
        group: str | None = None
        bucket = ""
        if upper.startswith("X-USED-WEIGHT-"):
            group = "used"
            bucket = upper.removeprefix("X-USED-WEIGHT-")
        elif upper.startswith("X-REMAINING-WEIGHT-"):
            group = "remaining"
            bucket = upper.removeprefix("X-REMAINING-WEIGHT-")
        if group is None or not _SAFE_RATE_BUCKET_PATTERN.fullmatch(bucket):
            continue
        try:
            parsed = int(str(value))
        except ValueError:
            continue
        if parsed >= 0:
            result[group][bucket] = parsed
    return result


def _contains_origin(value: Any, origin: str) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_origin(key, origin) or _contains_origin(item, origin) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_origin(item, origin) for item in value)
    hostname = urlsplit(origin).hostname or ""
    return (
        isinstance(value, str)
        and bool(hostname)
        and _normalize_leak_text(hostname) in _normalize_leak_text(value)
    )


def _redact_secret_literals(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, Mapping):
        return {
            _redact_secret_text(str(key), secrets): _redact_secret_literals(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_secret_literals(item, secrets) for item in value]
    if isinstance(value, str):
        return _redact_secret_text(value, secrets)
    return value


def _redact_secret_text(value: str, secrets: tuple[str, ...]) -> str:
    result = value
    for secret in secrets:
        if secret:
            result = re.sub(re.escape(secret), "***", result, flags=re.IGNORECASE)
    return result


def _normalize_leak_text(value: str) -> str:
    normalized = value
    for _ in range(4):
        previous = normalized
        normalized = normalized.replace("\\/", "/")
        normalized = re.sub(r"\\u002f", "/", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\\u002e", ".", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\\u003a", ":", normalized, flags=re.IGNORECASE)
        normalized = unquote(normalized)
        if normalized == previous:
            break
    return normalized.casefold()


def _redact_origin_content(value: Any, origin: str) -> Any:
    if isinstance(value, Mapping):
        return {
            ("***" if _contains_origin(str(key), origin) else str(key)): _redact_origin_content(item, origin)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_origin_content(item, origin) for item in value]
    if isinstance(value, str) and _contains_origin(value, origin):
        return "***"
    return value


def _default_transport(
    prepared: dict[str, Any],
    timeout_seconds: float,
) -> tuple[int, Mapping[str, Any], bytes]:
    request = Request(
        url=prepared["url"],
        method=prepared["method"],
        data=prepared["data"],
        headers=prepared["headers"],
    )
    try:
        with _open_no_redirect(request, timeout_seconds) as response:
            return int(response.status), dict(response.headers), response.read()
    except HTTPError as exc:
        try:
            return int(exc.code), dict(exc.headers or {}), exc.read()
        finally:
            exc.close()


def _open_no_redirect(request: Request, timeout_seconds: float) -> Any:
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout_seconds)
