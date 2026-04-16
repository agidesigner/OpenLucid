import importlib.machinery
import importlib.util
from pathlib import Path

import pytest


def load_cli_module():
    path = Path(__file__).resolve().parents[1] / "tools" / "openlucid-cli"
    loader = importlib.machinery.SourceFileLoader("openlucid_cli_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def cli():
    return load_cli_module()


def test_parser_accepts_extract_text(cli):
    parser = cli._build_parser()
    args = parser.parse_args(["extract-text", "--url", "https://example.com/product"])
    assert args.command == "extract-text"
    assert args.url is None
    assert args.page_url == "https://example.com/product"


def test_parser_accepts_create_offer(cli):
    parser = cli._build_parser()
    args = parser.parse_args([
        "create-offer",
        "--merchant-id", "merchant-1",
        "--name", "Test Offer",
        "--offer-type", "product",
        "--description", "desc",
        "--selling-points", "p1,p2",
        "--audiences", "a1,a2",
        "--scenarios", "s1,s2",
    ])
    assert args.command == "create-offer"
    assert args.merchant_id == "merchant-1"
    assert args.name == "Test Offer"
    assert args.selling_points == "p1,p2"


def test_parser_accepts_create_offer_from_url(cli):
    parser = cli._build_parser()
    args = parser.parse_args([
        "create-offer-from-url",
        "--merchant-id", "merchant-1",
        "--name", "Imported Offer",
        "--url", "https://example.com/product",
    ])
    assert args.command == "create-offer-from-url"
    assert args.url is None
    assert args.page_url == "https://example.com/product"


def test_cmd_extract_text_uses_form_request(cli, monkeypatch):
    calls = []

    def fake_request_form(base, token, cookie, method, path, form):
        calls.append((base, token, cookie, method, path, form))
        return {"text": "hello", "source": "url"}

    monkeypatch.setattr(cli, "_request_form", fake_request_form)

    class Args:
        page_url = "https://example.com/product"

    cli.cmd_extract_text("http://localhost", "token", "cookie", Args())
    assert calls == [
        ("http://localhost", "token", "cookie", "POST", "/ai/extract-text", {"url": "https://example.com/product"})
    ]


def test_cmd_create_offer_builds_expected_body(cli, monkeypatch):
    calls = []

    def fake_request(base, token, cookie, method, path, params=None, body=None):
        calls.append((base, token, cookie, method, path, params, body))
        return {"id": "offer-1"}

    monkeypatch.setattr(cli, "_request", fake_request)

    class Args:
        merchant_id = "merchant-1"
        name = "Test Offer"
        offer_type = "product"
        offer_model = "physical_product"
        description = "desc"
        positioning = "pos"
        selling_points = "point1, point2"
        audiences = "aud1,aud2"
        scenarios = "scene1,scene2"
        locale = "zh-CN"

    cli.cmd_create_offer("http://localhost", "token", "cookie", Args())
    assert calls[0][3] == "POST"
    assert calls[0][4] == "/offers"
    assert calls[0][6] == {
        "merchant_id": "merchant-1",
        "name": "Test Offer",
        "offer_type": "product",
        "offer_model": "physical_product",
        "description": "desc",
        "positioning": "pos",
        "core_selling_points_json": {"points": ["point1", "point2"]},
        "target_audience_json": {"items": ["aud1", "aud2"]},
        "target_scenarios_json": {"items": ["scene1", "scene2"]},
        "locale": "zh-CN",
    }


def test_cmd_create_offer_from_url_extracts_then_creates(cli, monkeypatch):
    extracted = {"text": "product text from url", "source": "url"}
    inferred = {
        "offer_name": "Imported Offer",
        "description": "ai polished description",
        "suggestions": {
            "selling_point": [
                {"knowledge_type": "selling_point", "title": "3倍洁净力", "content_raw": "去污更强"},
            ],
            "audience": [
                {"knowledge_type": "audience", "title": "家庭用户", "content_raw": "日常洗衣人群"},
            ],
            "scenario": [
                {"knowledge_type": "scenario", "title": "日常洗衣", "content_raw": "日常衣物清洁"},
            ],
        },
    }
    calls = []

    def fake_extract(base, token, cookie, url):
        return extracted

    monkeypatch.setattr(cli, "_extract_text_from_url", fake_extract)
    monkeypatch.setattr(cli, "_infer_offer_knowledge", lambda base, token, cookie, args, description: inferred)

    def fake_request(base, token, cookie, method, path, params=None, body=None):
        calls.append((method, path, body))
        if path == "/offers":
            return {"id": "offer-1", **body}
        if path == "/knowledge/batch":
            return {"created": len(body["items"]), "items": body["items"]}
        return body

    monkeypatch.setattr(cli, "_request", fake_request)

    class Args:
        merchant_id = "merchant-1"
        name = "Imported Offer"
        page_url = "https://example.com/product"
        offer_type = "product"
        offer_model = None
        positioning = ""
        selling_points = ""
        audiences = ""
        scenarios = ""
        locale = "zh-CN"

    result = cli.cmd_create_offer_from_url("http://localhost", "token", "cookie", Args())
    assert result["offer"]["description"] == "ai polished description"
    assert result["offer"]["core_selling_points_json"] == {"points": ["3倍洁净力"]}
    assert result["offer"]["target_audience_json"] == {"items": ["家庭用户"]}
    assert result["offer"]["target_scenarios_json"] == {"items": ["日常洗衣"]}
    assert result["knowledge_batch"]["created"] == 3
    assert calls[0][1] == "/offers"
    assert calls[1][1] == "/knowledge/batch"


def test_cmd_create_offer_from_url_falls_back_when_infer_unavailable(cli, monkeypatch):
    extracted = {"text": "product text from url", "source": "url"}

    monkeypatch.setattr(cli, "_extract_text_from_url", lambda base, token, cookie, url: extracted)
    monkeypatch.setattr(cli, "_infer_offer_knowledge", lambda base, token, cookie, args, description: None)
    monkeypatch.setattr(cli, "_request", lambda base, token, cookie, method, path, params=None, body=None: {"id": "offer-1", **body})

    class Args:
        merchant_id = "merchant-1"
        name = "Imported Offer"
        page_url = "https://example.com/product"
        offer_type = "product"
        offer_model = None
        positioning = ""
        selling_points = ""
        audiences = ""
        scenarios = ""
        locale = "zh-CN"

    result = cli.cmd_create_offer_from_url("http://localhost", "token", "cookie", Args())
    assert result["offer"]["description"] == "product text from url"
    assert result["knowledge_batch"] is None
