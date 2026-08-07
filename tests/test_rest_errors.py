import unittest


class RestErrorTests(unittest.TestCase):
    def test_resource_item_code_is_not_treated_as_envelope_error(self):
        from weex_mcp.adapters.rest_errors import raise_for_weex_business_error

        raise_for_weex_business_error({"code": "0", "data": [{"code": "BID", "price": "1"}]})


if __name__ == "__main__":
    unittest.main()
