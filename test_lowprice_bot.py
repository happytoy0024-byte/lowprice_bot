#!/usr/bin/env python3
"""Mock-data verification suite for lowprice_bot.py (zero real network requests)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import runpy
import tempfile
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest.mock import patch

BOT_PATH = Path(__file__).resolve().parent / "lowprice_bot.py"


class MockHTTPResponse:
    """Minimal file-like HTTP response object for mocking urllib.request.urlopen."""

    def __init__(self, body: str, status: int = 200) -> None:
        self._data = body.encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> "MockHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def build_product_card(
    pcode: str,
    title: str,
    price: int,
    *,
    pack_label: str = "20팩",
    review: str = "120",
    rel: str = "",
    extra_html: str = "",
    bridge_url: str | None = None,
) -> str:
    href = bridge_url or f"https://prod.danawa.com/info/?pcode={pcode}&keyword=%EB%B2%A0%EB%B2%A0%EC%88%B2+20%EA%B0%9C"
    rel_attr = f' rel="{rel}"' if rel else ""
    return f"""
    <li class="border-b">
      {extra_html}
      <a href="{href}"{rel_attr}>{title}</a>
      <span>리뷰수 ( {review} )</span>
      <div class="flex">
        <span>{pack_label}</span>
        <a href="https://prod.danawa.com/info/?pcode={pcode}">{price:,}원</a>
      </div>
    </li>
    """


def build_search_html(cards_html: str, ad_section_html: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head><title>베베숲 20개 : 다나와 통합검색</title></head>
<body>
  {ad_section_html}
  <ul>
    {cards_html}
  </ul>
</body>
</html>
"""


def build_detail_html(pcode: str, title: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head><title>{title} : 다나와</title></head>
<body>
  <div class="prod_tit"><h3 class="title">{title}</h3></div>
  <script>
    var oProduct = {{
      nCategoryCode1: "1985",
      nCategoryCode2: "227644",
      nCategoryCode3: "227662",
      nCategoryCode4: "227749",
      nCategoryCode: "17341751",
      nMinPrice: "20000",
      sProductName: "{title}"
    }};
  </script>
</body>
</html>
"""


def build_offers_html(pcode: str, mall: str, price: int, shipping_text: str) -> str:
    buy_url = f"https://prod.danawa.com/bridge/loadingBridge.html?pcode={pcode}&mall={urllib.parse.quote(mall)}"
    return f"""
    <div class="diff_item">
      <div class="d_mall"><img src="/mall.png" alt="{mall}" /></div>
      <div class="prc_line"><em class="prc_c">{price:,}</em>원</div>
      <div class="ship">{shipping_text}</div>
      <a class="priceCompareBuyLink" href="{buy_url}">사러가기</a>
    </div>
    """


def make_mock_urlopen(
    search_html: str,
    products: Dict[str, Tuple[str, str, int, str]],
    *,
    kakao_calls: Optional[List[Dict[str, object]]] = None,
    kakao_token_error: Optional[Exception] = None,
    kakao_memo_response: Optional[str] = None,
    kakao_memo_error: Optional[Exception] = None,
):
    """Return a fake urlopen function keyed by Danawa + Kakao URLs."""

    def _fake_urlopen(req, timeout=20):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "search.danawa.com/dsearch.php" in url:
            return MockHTTPResponse(search_html)
        if "prod.danawa.com/info/?pcode=" in url:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            pcode = qs.get("pcode", [""])[0]
            if pcode not in products:
                raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
            title = products[pcode][0]
            return MockHTTPResponse(build_detail_html(pcode, title))
        if "getAllPriceCompareMallList.ajax.php" in url:
            body_bytes = req.data or b""
            params = urllib.parse.parse_qs(body_bytes.decode("utf-8"))
            pcode = params.get("pcode", [""])[0]
            if pcode not in products:
                return MockHTTPResponse("<div>no offers</div>")
            _, mall, item_price, shipping_text = products[pcode]
            return MockHTTPResponse(build_offers_html(pcode, mall, item_price, shipping_text))
        if "kauth.kakao.com/oauth/token" in url:
            body_params = urllib.parse.parse_qs((req.data or b"").decode("utf-8"))
            if kakao_calls is not None:
                kakao_calls.append({"url": url, "params": body_params})
            if kakao_token_error is not None:
                raise kakao_token_error
            return MockHTTPResponse(json.dumps({"access_token": "mock_access_token"}))
        if "kapi.kakao.com/v2/api/talk/memo/default/send" in url:
            body_params = urllib.parse.parse_qs((req.data or b"").decode("utf-8"))
            template = json.loads(body_params["template_object"][0])
            if kakao_calls is not None:
                kakao_calls.append({"url": url, "template": template})
            if kakao_memo_error is not None:
                raise kakao_memo_error
            return MockHTTPResponse(kakao_memo_response or json.dumps({"result_code": 0}))
        raise AssertionError(f"Unexpected external URL requested during mock test: {url}")

    return _fake_urlopen


def run_bot_with_mock(
    mock_urlopen,
    *,
    env_content: Optional[str] = None,
    missing_env: bool = False,
) -> Tuple[int, str, str]:
    """Execute lowprice_bot.py as __main__ under mocked urlopen and an isolated .env."""
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    exit_code = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"
        if not missing_env:
            default_env = (
                "kakao_REST_API_key=test_rest_key\n"
                "kakao_CLIENT_SECRET=test_client_secret\n"
                "KAKAO_REFRESH_TOKEN=test_refresh_token\n"
            )
            env_file.write_text(default_env if env_content is None else env_content, encoding="utf-8")

        env_patch = {"LOWPRICE_ENV_PATH": str(env_file)}
        with (
            patch.dict(os.environ, env_patch, clear=False),
            patch("urllib.request.urlopen", side_effect=mock_urlopen),
            patch("time.sleep", return_value=None),
            contextlib.redirect_stdout(stdout_buf),
            contextlib.redirect_stderr(stderr_buf),
        ):
            try:
                runpy.run_path(str(BOT_PATH), run_name="__main__")
            except SystemExit as exc:
                exit_code = exc.code if isinstance(exc.code, int) else 1
    return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()


def test_1_under_or_equal_target() -> None:
    """1. 20000원 이하일 때 특가 메시지 및 카카오톡 알림 발송 확인"""
    # Case A: 19,500원 (상품가 16,500원 + 배송비 3,000원) -> 목표가 미만
    products_under = {
        "101": ("베베숲 테스트A 물티슈 70매 (20팩)", "G마켓", 16500, "배송비 3,000원"),
        "102": ("베베숲 테스트B 물티슈 80매 (20팩)", "옥션", 21000, "무료배송"),
        "103": ("베베숲 테스트C 물티슈 70매 (20팩)", "11번가", 22500, "무료배송"),
    }
    cards_under = "".join(
        build_product_card(p, t, pr, review="350")
        for p, (t, _, pr, _) in products_under.items()
    )
    kakao_calls: List[Dict[str, object]] = []
    code, out, err = run_bot_with_mock(
        make_mock_urlopen(build_search_html(cards_under), products_under, kakao_calls=kakao_calls)
    )
    assert code == 0, f"19,500원 테스트 비정상 종료(code={code}): {err}"
    assert "[특가발견] 목표 가격 이하입니다!" in out, f"특가 메시지 누락:\n{out}"
    assert "[카카오톡 알림] '나에게 보내기' 메시지 발송 완료!" in out, f"카카오 알림 성공 메시지 누락:\n{out}"
    assert "[유지] 아직 목표 가격보다 비싸요!" not in out, f"유지 메시지가 잘못 출력됨:\n{out}"
    for expected in ("19,500원", "G마켓", "350", "[1위]", "[2위]", "[3위]"):
        assert expected in out, f"출력 항목 누락({expected}):\n{out}"

    memo_calls = [c for c in kakao_calls if "memo/default/send" in str(c["url"])]
    assert len(memo_calls) == 1, f"카카오톡 나에게 보내기가 1회 호출되어야 함: {kakao_calls}"
    tpl = memo_calls[0]["template"]
    assert tpl["object_type"] == "text", f"잘못된 object_type: {tpl}"
    assert tpl["button_title"] == "구매하러 가기", f"버튼명 불일치: {tpl}"
    assert "pcode=101" in tpl["link"]["web_url"] and "pcode=101" in tpl["link"]["mobile_web_url"], f"링크 불일치: {tpl}"
    for field_val in ("베베숲 테스트A 물티슈 70매 (20팩)", "19,500원", "G마켓", "350"):
        assert field_val in tpl["text"], f"메시지 본문에 {field_val} 누락: {tpl['text']}"

    # Case B: 정확히 20,000원 (경계값 이하 포함 확인)
    products_exact = {
        "201": ("베베숲 경계값 물티슈 70매 (20팩)", "롯데ON", 20000, "무료배송"),
        "202": ("베베숲 테스트B 물티슈 80매 (20팩)", "옥션", 21500, "무료배송"),
        "203": ("베베숲 테스트C 물티슈 70매 (20팩)", "11번가", 23000, "무료배송"),
    }
    cards_exact = "".join(
        build_product_card(p, t, pr, review="999+")
        for p, (t, _, pr, _) in products_exact.items()
    )
    kakao_calls_exact: List[Dict[str, object]] = []
    code, out, err = run_bot_with_mock(
        make_mock_urlopen(build_search_html(cards_exact), products_exact, kakao_calls=kakao_calls_exact)
    )
    assert code == 0, f"20,000원 경계값 테스트 비정상 종료(code={code}): {err}"
    assert "[특가발견] 목표 가격 이하입니다!" in out, f"20,000원 경계값에서 특가 메시지 누락:\n{out}"
    assert len([c for c in kakao_calls_exact if "memo/default/send" in str(c["url"])]) == 1


def test_2_over_target() -> None:
    """2. 20000원 초과일 때 유지 메시지 출력 및 카카오톡 미발송 확인"""
    products_over = {
        "301": ("베베숲 초과테스트1 물티슈 70매 (20팩)", "G마켓", 20010, "무료배송"),
        "302": ("베베숲 초과테스트2 물티슈 80매 (20팩)", "옥션", 19000, "배송비 3,500원"),
        "303": ("베베숲 초과테스트3 물티슈 70매 (20팩)", "11번가", 24000, "무료배송"),
    }
    cards = "".join(
        build_product_card(p, t, pr, review="512")
        for p, (t, _, pr, _) in products_over.items()
    )
    kakao_calls: List[Dict[str, object]] = []
    code, out, err = run_bot_with_mock(
        make_mock_urlopen(build_search_html(cards), products_over, kakao_calls=kakao_calls)
    )
    assert code == 0, f"20,000원 초과 테스트 비정상 종료(code={code}): {err}"
    assert "[유지] 아직 목표 가격보다 비싸요!" in out, f"유지 메시지 누락:\n{out}"
    assert "[특가발견] 목표 가격 이하입니다!" not in out, f"특가 메시지가 잘못 출력됨:\n{out}"
    assert "20,010원" in out and "22,500원" in out and "24,000원" in out, f"실구매가 출력 누락:\n{out}"
    assert len(kakao_calls) == 0, f"목표가 초과 시 카카오 API가 호출되면 안 됨: {kakao_calls}"


def test_3_filter_ad_products() -> None:
    """3. 광고 상품이 섞여 있을 때 걸러낼 수 있는가?"""
    real_products = {
        "401": ("베베숲 진짜상품1 물티슈 70매 (20팩)", "G마켓", 21500, "무료배송"),
        "402": ("베베숲 진짜상품2 물티슈 80매 (20팩)", "옥션", 22500, "무료배송"),
        "403": ("베베숲 진짜상품3 물티슈 70매 (20팩)", "11번가", 23500, "무료배송"),
    }
    ad_container = f"""
    <div aria-label="파워쇼핑 광고 상품 목록">
      <ul>
        {build_product_card("9991", "베베숲 파워쇼핑광고 물티슈 (20팩)", 5000)}
      </ul>
    </div>
    """
    ad_sponsored = build_product_card("9992", "베베숲 스폰서광고 물티슈 (20팩)", 6000, rel="noopener nofollow sponsored")
    ad_badge = build_product_card("9993", "베베숲 배지광고 물티슈 (20팩)", 7000, extra_html="<span>광고</span>")
    ad_bridge = build_product_card(
        "9994",
        "베베숲 브릿지광고 물티슈 (20팩)",
        8000,
        bridge_url="https://prod.danawa.com/bridge/loadingBridgePowerShopping.php?pcode=9994",
    )
    wrong_pack = build_product_card("9995", "베베숲 프라임 물티슈 캡형 20매", 8990, pack_label="12팩")

    real_cards = "".join(
        build_product_card(p, t, pr, review="777")
        for p, (t, _, pr, _) in real_products.items()
    )
    html = build_search_html(ad_sponsored + ad_badge + ad_bridge + wrong_pack + real_cards, ad_section_html=ad_container)

    all_products = dict(real_products)
    all_products.update({
        "9991": ("베베숲 파워쇼핑광고 물티슈 (20팩)", "광고몰", 5000, "무료배송"),
        "9992": ("베베숲 스폰서광고 물티슈 (20팩)", "광고몰", 6000, "무료배송"),
        "9993": ("베베숲 배지광고 물티슈 (20팩)", "광고몰", 7000, "무료배송"),
        "9994": ("베베숲 브릿지광고 물티슈 (20팩)", "광고몰", 8000, "무료배송"),
        "9995": ("베베숲 프라임 물티슈 캡형 20매", "일반몰", 8990, "무료배송"),
    })

    kakao_calls: List[Dict[str, object]] = []
    code, out, err = run_bot_with_mock(
        make_mock_urlopen(html, all_products, kakao_calls=kakao_calls)
    )
    assert code == 0, f"광고 필터링 테스트 비정상 종료(code={code}): {err}"
    for forbidden in ("파워쇼핑광고", "스폰서광고", "배지광고", "브릿지광고", "광고몰", "12팩", "5,000원", "6,000원", "7,000원", "8,000원", "8,990원"):
        assert forbidden not in out, f"광고 또는 비대상 상품({forbidden})이 필터링되지 않음:\n{out}"
    for expected in ("베베숲 진짜상품1", "베베숲 진짜상품2", "베베숲 진짜상품3", "[유지] 아직 목표 가격보다 비싸요!"):
        assert expected in out, f"진짜 상품 출력 누락({expected}):\n{out}"
    assert len(kakao_calls) == 0, "광고가 걸러졌다면 실제 1위가 21,500원이므로 카카오 알림이 호출되지 않아야 함"


def test_4_error_handling_without_crashing_or_faking() -> None:
    """4. 값을 가져오지 못했을 경우 멈추지 않고 오류를 알려주는가?"""
    # 4a. 네트워크 연결 오류
    def _net_err(req, timeout=20):
        raise urllib.error.URLError("mock connection refused")

    code, out, err = run_bot_with_mock(_net_err)
    assert code == 1, f"네트워크 오류 시 종료코드 1이어야 함(code={code})"
    assert "[오류 - 실행 중단]" in err and "네트워크 연결 실패" in err, f"오류 메시지 미출력:\n{err}"
    assert "[1위]" not in out, f"실패 시 가짜 결과 출력 금지:\n{out}"

    # 4b. HTTP 500 오류
    def _http_500(req, timeout=20):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        raise urllib.error.HTTPError(url, 500, "Internal Server Error", None, None)

    code, out, err = run_bot_with_mock(_http_500)
    assert code == 1, f"HTTP 500 오류 시 종료코드 1이어야 함(code={code})"
    assert "[오류 - 실행 중단]" in err and "500" in err, f"HTTP 상태 오류 메시지 미출력:\n{err}"
    assert "[1위]" not in out, f"실패 시 가짜 결과 출력 금지:\n{out}"

    # 4c. 빈 응답
    def _empty_resp(req, timeout=20):
        return MockHTTPResponse("   ")

    code, out, err = run_bot_with_mock(_empty_resp)
    assert code == 1, f"빈 응답 시 종료코드 1이어야 함(code={code})"
    assert "[오류 - 실행 중단]" in err and "빈 응답" in err, f"빈 응답 오류 메시지 미출력:\n{err}"
    assert "[1위]" not in out, f"실패 시 가짜 결과 출력 금지:\n{out}"

    # 4d. 상품 카드가 없는 변경된 HTML
    def _broken_html(req, timeout=20):
        return MockHTTPResponse("<html><body>다나와 변경된 구조</body></html>")

    code, out, err = run_bot_with_mock(_broken_html)
    assert code == 1, f"HTML 구조 불일치 시 종료코드 1이어야 함(code={code})"
    assert "[오류 - 실행 중단]" in err and "상품 카드" in err, f"HTML 구조 오류 메시지 미출력:\n{err}"
    assert "[1위]" not in out, f"실패 시 가짜 결과 출력 금지:\n{out}"

    # 4e. 검색은 성공했으나 쇼핑몰 가격비교(offers)를 가져오지 못한 경우
    cards = "".join(
        build_product_card(p, f"베베숲 실패테스트{i} (20팩)", 21000)
        for i, p in enumerate(("501", "502", "503"), start=1)
    )

    def _empty_offers_urlopen(req, timeout=20):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "search.danawa.com/dsearch.php" in url:
            return MockHTTPResponse(build_search_html(cards))
        if "prod.danawa.com/info/?pcode=" in url:
            return MockHTTPResponse(build_detail_html("501", "베베숲 실패테스트 (20팩)"))
        if "getAllPriceCompareMallList.ajax.php" in url:
            return MockHTTPResponse("<div>오퍼 없음</div>")
        return MockHTTPResponse("")

    code, out, err = run_bot_with_mock(_empty_offers_urlopen)
    assert code == 1, f"오퍼 누락 시 종료코드 1이어야 함(code={code})"
    assert "[오류 - 실행 중단]" in err and "가격비교" in err, f"오퍼 누락 오류 메시지 미출력:\n{err}"
    assert "[1위]" not in out, f"실패 시 가짜 결과 출력 금지:\n{out}"


def test_5_kakao_failure_reports_exact_reason() -> None:
    """5. 카카오톡 알림 발송 실패 시 보낸 척 하지 않고 정확한 원인 안내 확인"""
    products_under = {
        "601": ("베베숲 특가상품1 물티슈 70매 (20팩)", "G마켓", 18000, "무료배송"),
        "602": ("베베숲 특가상품2 물티슈 80매 (20팩)", "옥션", 21000, "무료배송"),
        "603": ("베베숲 특가상품3 물티슈 70매 (20팩)", "11번가", 22000, "무료배송"),
    }
    cards = "".join(
        build_product_card(p, t, pr, review="200")
        for p, (t, _, pr, _) in products_under.items()
    )
    search_html = build_search_html(cards)

    # 5a. .env에 KAKAO_REFRESH_TOKEN 누락
    code, out, err = run_bot_with_mock(
        make_mock_urlopen(search_html, products_under),
        env_content="kakao_REST_API_key=test_rest_key\nkakao_CLIENT_SECRET=test_secret\n",
    )
    assert code == 1, f"리프레시 토큰 누락 시 종료코드 1이어야 함(code={code})"
    assert "발송 완료" not in out, f"발송 실패 시 완료 문구를 출력하면 안 됨:\n{out}"
    assert "[오류 - 실행 중단]" in err and "KAKAO_REFRESH_TOKEN" in err, f"토큰 누락 원인 미출력:\n{err}"

    # 5b. 카카오 나에게 보내기 API 실패 (HTTP 400 / -402 등)
    memo_err = urllib.error.HTTPError(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        400,
        "Bad Request",
        None,
        io.BytesIO(json.dumps({"code": -402, "msg": "insufficient scopes"}).encode("utf-8")),
    )
    code, out, err = run_bot_with_mock(
        make_mock_urlopen(search_html, products_under, kakao_memo_error=memo_err)
    )
    assert code == 1, f"카카오 API 오류 시 종료코드 1이어야 함(code={code})"
    assert "발송 완료" not in out, f"발송 실패 시 완료 문구를 출력하면 안 됨:\n{out}"
    assert "[오류 - 실행 중단]" in err and "-402" in err and "insufficient scopes" in err, f"카카오 오류 상세 미출력:\n{err}"


def main() -> int:
    checks = [
        ("1. 20000원 이하일 때 특가 메시지 및 카카오톡 알림 발송 확인", test_1_under_or_equal_target),
        ("2. 20000원 초과일 때 유지 메시지 출력 및 카카오톡 미발송 확인", test_2_over_target),
        ("3. 광고 상품 필터링 확인", test_3_filter_ad_products),
        ("4. 값 미수신/오류 시 멈추지 않고 오류 안내 확인", test_4_error_handling_without_crashing_or_faking),
        ("5. 카카오톡 발송 실패 시 보낸 척 하지 않고 원인 안내 확인", test_5_kakao_failure_reports_exact_reason),
    ]

    passed = 0
    for label, fn in checks:
        try:
            fn()
            print(f"[PASS] {label}")
            passed += 1
        except Exception as exc:
            print(f"[FAIL] {label} -> {type(exc).__name__}: {exc}")

    print(f"\n총 {len(checks)}개 중 {passed}개 PASS, {len(checks) - passed}개 FAIL")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
