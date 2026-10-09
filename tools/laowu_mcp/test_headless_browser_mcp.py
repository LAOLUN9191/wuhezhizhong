import json
import unittest
from unittest.mock import patch

import headless_browser_mcp as browser_mcp


class HeadlessBrowserTests(unittest.TestCase):
    def test_search_url_encodes_generic_query_and_result_limit(self):
        url = browser_mcp.build_search_url("Codex CLI web search", 3)

        self.assertEqual(
            url,
            "https://www.bing.com/search?q=Codex+CLI+web+search&count=3",
        )

    def test_search_rejects_empty_and_invalid_result_counts(self):
        with self.assertRaises(ValueError):
            browser_mcp.build_search_url(" ")
        with self.assertRaises(ValueError):
            browser_mcp.build_search_url("public query", 11)
        with self.assertRaises(ValueError):
            browser_mcp.build_search_url("public query", True)

    def test_public_page_url_blocks_local_and_non_https_targets(self):
        self.assertEqual(browser_mcp.validate_public_url("https://example.com/docs"), "https://example.com/docs")
        for url in (
            "file:///C:/Windows/win.ini", "http://example.com", "https://localhost/",
            "https://127.0.0.1/", "https://192.168.1.1/", "https://user:pass@example.com/",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                browser_mcp.validate_public_url(url)

    def test_tools_are_read_only_and_expose_only_search_and_page_read(self):
        tools = browser_mcp._tools()

        self.assertEqual([tool["name"] for tool in tools], ["search", "open_page"])
        self.assertTrue(all(tool["annotations"]["readOnlyHint"] for tool in tools))
        self.assertTrue(all(tool["annotations"]["openWorldHint"] for tool in tools))

    def test_search_decodes_browser_results(self):
        browser = browser_mcp.HeadlessBrowser()
        results = [{"title": "Example", "url": "https://example.com/", "snippet": "Example page"}]
        with patch.object(browser, "_run_cdp", side_effect=["opened", json.dumps(json.dumps(results))]) as run:
            found = browser.search("public query", 1)

        self.assertEqual(found, results)
        self.assertEqual(run.call_count, 2)

    def test_open_page_returns_title_url_and_text(self):
        browser = browser_mcp.HeadlessBrowser()
        page = {"title": "Example", "url": "https://example.com/", "text": "Public page"}
        with patch.object(browser, "_run_cdp", side_effect=["opened", json.dumps(json.dumps(page))]):
            result = browser.open_page("https://example.com/")

        self.assertEqual(result, page)

    def test_mcp_tools_list_has_only_read_only_browser_tools(self):
        response = browser_mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

        self.assertEqual(
            [tool["name"] for tool in response["result"]["tools"]],
            ["search", "open_page"],
        )

    def test_browser_control_uses_direct_cdp_without_agent_browser_daemon(self):
        self.assertTrue(browser_mcp.NODE.is_file())
        self.assertTrue(browser_mcp.CDP_CLIENT.is_file())
        self.assertFalse(hasattr(browser_mcp, "AGENT_BROWSER"))


if __name__ == "__main__":
    unittest.main()
