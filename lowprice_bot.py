#!/usr/bin/env python3
"""Danawa low-price bot for '베베숲 20개' using the danawa-price-search public surface."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Dict, List, Optional

QUERY = "베베숲 20개"
TARGET_PRICE = 20000
TOP_N = 3
CANDIDATE_SCAN_LIMIT = 8
REQUEST_TIMEOUT = 20
ENV_PATH = Path(os.environ.get("LOWPRICE_ENV_PATH") or (Path(__file__).resolve().parent / ".env"))
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_MEMO_SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
KAKAO_TEXT_MAX_LEN = 200
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/121 Safari/537.36"
)

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class BotError(RuntimeError):
    """Raised when fetched data is missing, blocked, or invalid."""


class Node:
    """Lightweight DOM node built with Python's standard HTMLParser."""

    def __init__(self, tag: str, attrs: Optional[Dict[str, str]] = None, parent: Optional["Node"] = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.parent = parent
        # Store child Nodes and raw text strings in document order so .text() preserves sequence.
        self.children: List[object] = []

    def classes(self) -> List[str]:
        return self.attrs.get("class", "").split()

    def has_class(self, name: str) -> bool:
        return name in self.classes()

    def attr(self, name: str, default: str = "") -> str:
        return self.attrs.get(name, default)

    def text(self) -> str:
        buf: List[str] = []

        def _collect(node: "Node") -> None:
            for child in node.children:
                if isinstance(child, str):
                    buf.append(child)
                else:
                    _collect(child)

        _collect(self)
        return " ".join(unescape(" ".join(buf)).split())

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def find_all(self, predicate: Callable[["Node"], bool]) -> List["Node"]:
        return [n for n in self.walk() if predicate(n)]

    def find_first(self, predicate: Callable[["Node"], bool]) -> Optional["Node"]:
        for n in self.walk():
            if predicate(n):
                return n
        return None

    def ancestors(self):
        cur = self.parent
        while cur is not None:
            yield cur
            cur = cur.parent


class TreeBuilder(HTMLParser):
    """HTMLParser that constructs a Node tree without external dependencies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.root = Node("document")
        self.current = self.root

    def handle_starttag(self, tag: str, attrs) -> None:
        attr_dict = {k.lower(): (v if v is not None else "") for k, v in attrs}
        node = Node(tag.lower(), attr_dict, parent=self.current)
        self.current.children.append(node)
        if tag.lower() not in VOID_TAGS:
            self.current = node

    def handle_startendtag(self, tag: str, attrs) -> None:
        attr_dict = {k.lower(): (v if v is not None else "") for k, v in attrs}
        node = Node(tag.lower(), attr_dict, parent=self.current)
        self.current.children.append(node)

    def handle_endtag(self, tag: str) -> None:
        target = tag.lower()
        cur = self.current
        while cur is not None and cur is not self.root:
            if cur.tag == target:
                self.current = cur.parent or self.root
                return
            cur = cur.parent

    def handle_data(self, data: str) -> None:
        if self.current.tag not in ("script", "style") and data.strip():
            self.current.children.append(data)

    def handle_entityref(self, name: str) -> None:
        if self.current.tag not in ("script", "style"):
            self.current.children.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self.current.tag not in ("script", "style"):
            self.current.children.append(f"&#{name};")


def parse_html(html: str) -> Node:
    builder = TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def parse_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def abs_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://prod.danawa.com" + url
    return url


def fetch(url: str, *, data: Optional[dict] = None, referer: Optional[str] = None) -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    }
    body = None
    method = "GET"
    if data is not None:
        method = "POST"
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        headers["X-Requested-With"] = "XMLHttpRequest"
    if referer:
        headers["Referer"] = referer

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            status = getattr(resp, "status", 200)
            if status >= 400:
                raise BotError(f"HTTP 오류 응답({status}): {url}")
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise BotError(f"HTTP 요청 실패(status={exc.code}): {url}") from exc
    except urllib.error.URLError as exc:
        raise BotError(f"네트워크 연결 실패({exc.reason}): {url}") from exc
    except TimeoutError as exc:
        raise BotError(f"요청 시간 초과({REQUEST_TIMEOUT}s): {url}") from exc
    except OSError as exc:
        raise BotError(f"네트워크/소켓 오류({exc}): {url}") from exc

    if not raw.strip():
        raise BotError(f"빈 응답을 받았습니다: {url}")

    lowered = raw.lower()
    if any(marker in lowered for marker in ("captcha", "자동입력 방지", "보안문자", "access denied")):
        raise BotError(f"접근 제한/CAPTCHA가 감지되어 중단합니다: {url}")
    return raw


def is_ad_card(li: Node) -> bool:
    for anc in li.ancestors():
        if "광고" in anc.attr("aria-label"):
            return True
    for node in li.walk():
        if node.tag == "a":
            href = node.attr("href")
            rel = node.attr("rel").lower()
            if "sponsored" in rel:
                return True
            if "loadingbridgepowershopping" in href.lower() or "ads-partners.coupang.com" in href.lower():
                return True
        if node.tag in ("span", "div") and node.text() in ("광고", "AD", "스폰서"):
            return True
    return False


def extract_review_count(card_text: str) -> str:
    m = re.search(r"리뷰수\s*\(\s*([^)]+?)\s*\)", card_text)
    if not m:
        return "표기 없음"
    return m.group(1).strip() or "표기 없음"


def extract_pack_price_rows(li: Node) -> List[Dict[str, object]]:
    """Extract (pcode, pack_label, price) from a Danawa product card."""
    rows: List[Dict[str, object]] = []
    for div in li.find_all(lambda n: n.tag == "div" and n.has_class("flex")):
        txt = div.text()
        if not txt or "원" not in txt or len(txt) > 140:
            continue
        pack_m = re.search(r"(?<!\d)20\s*(?:팩|개|입)(?!\d)", txt)
        if not pack_m:
            continue
        prices = [int(p.replace(",", "")) for p in re.findall(r"(\d{1,3}(?:,\d{3})+)원(?!/)", txt)]
        if not prices:
            continue
        pcode = None
        for a in div.find_all(lambda n: n.tag == "a" and "pcode=" in n.attr("href")):
            pm = re.search(r"pcode=(\d+)", a.attr("href"))
            if pm:
                pcode = pm.group(1)
                break
        if pcode:
            rows.append({
                "pcode": pcode,
                "pack_label": re.sub(r"\s+", "", pack_m.group(0)),
                "price": min(prices),
            })
    return rows


def search_candidates(query: str) -> List[Dict[str, object]]:
    url = "https://search.danawa.com/dsearch.php?query=" + urllib.parse.quote(query)
    html = fetch(url)
    if "다나와" not in html:
        raise BotError(f"다나와 검색 페이지 형식이 아닙니다: {url}")

    root = parse_html(html)
    cards = root.find_all(lambda n: n.tag == "li" and n.has_class("border-b"))
    if not cards:
        raise BotError("다나와 검색 HTML에서 상품 카드(li.border-b)를 찾지 못했습니다. HTML 구조가 변경되었을 수 있습니다.")

    candidates: Dict[str, Dict[str, object]] = {}
    for li in cards:
        if is_ad_card(li):
            continue

        prod_links = li.find_all(
            lambda n: n.tag == "a"
            and "prod.danawa.com/info/?pcode=" in n.attr("href")
            and "sponsored" not in n.attr("rel").lower()
        )
        if not prod_links:
            continue

        title = None
        main_pcode = None
        for a in prod_links:
            t = a.text()
            if not t or t == "이미지 보기" or "원" in t or "리뷰" in t or "의견" in t:
                continue
            pm = re.search(r"pcode=(\d+)", a.attr("href"))
            if pm:
                title = t
                main_pcode = pm.group(1)
                break
        if not title or not main_pcode:
            continue
        if "베베숲" not in title:
            continue

        card_text = li.text()
        review_count = extract_review_count(card_text)

        pack_rows = extract_pack_price_rows(li)
        if not pack_rows:
            # Check title/card text only if it explicitly indicates 20팩/20개/20입 (not just 20매)
            cleaned_for_pack = re.sub(r"\d+\s*매", "", card_text)
            if not re.search(r"(?<!\d)20\s*(?:팩|개|입)(?!\d)", cleaned_for_pack):
                continue
            prices = [int(p.replace(",", "")) for p in re.findall(r"(\d{1,3}(?:,\d{3})+)원(?!/)", card_text)]
            if not prices:
                continue
            pack_rows = [{"pcode": main_pcode, "pack_label": "20팩", "price": min(prices)}]

        for row in pack_rows:
            pcode = str(row["pcode"])
            price = int(row["price"])
            if price <= 0:
                continue
            pack_label = str(row["pack_label"])
            full_title = title if pack_label in title else f"{title} ({pack_label})"
            prev = candidates.get(pcode)
            if prev is None or price < int(prev["search_price"]):
                candidates[pcode] = {
                    "pcode": pcode,
                    "title": full_title,
                    "search_price": price,
                    "review_count": review_count,
                    "product_url": f"https://prod.danawa.com/info/?pcode={pcode}",
                }

    ordered = sorted(candidates.values(), key=lambda item: int(item["search_price"]))
    if not ordered:
        raise BotError(f"검색어 '{query}'에 해당하는 광고 제외 '20개(20팩)' 카탈로그 상품을 찾지 못했습니다.")
    return ordered


def js_value(html: str, key: str) -> str:
    patterns = [
        rf'{re.escape(key)}\s*:\s*"([^"]*)"',
        rf"{re.escape(key)}\s*:\s*'([^']*)'",
        rf"{re.escape(key)}\s*:\s*([0-9]+)",
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            raw = m.group(1)
            if "\\u" in raw or "\\/" in raw:
                try:
                    return json.loads('"' + raw.replace('"', '\\"') + '"')
                except Exception:
                    return raw.replace("\\/", "/")
            return raw
    return ""


def fetch_lowest_offer(pcode: str) -> Dict[str, object]:
    product_url = f"https://prod.danawa.com/info/?pcode={urllib.parse.quote(pcode)}"
    detail_html = fetch(product_url)
    detail_root = parse_html(detail_html)

    full_title = js_value(detail_html, "sProductName")
    title_node = detail_root.find_first(lambda n: n.has_class("title") and any(a.has_class("prod_tit") for a in n.ancestors()))
    if title_node and title_node.text():
        full_title = title_node.text()

    ajax_data = {
        "pcode": pcode,
        "cate1": js_value(detail_html, "nCategoryCode1"),
        "cate2": js_value(detail_html, "nCategoryCode2"),
        "cate3": js_value(detail_html, "nCategoryCode3"),
        "cate4": js_value(detail_html, "nCategoryCode4") or "0",
        "UICategoryCode": js_value(detail_html, "nCategoryCode") or "0",
        "powerLinkKeyword": js_value(detail_html, "powerLinkKeyword"),
        "minPrice": js_value(detail_html, "nMinPrice"),
        "keyword": js_value(detail_html, "sKeyword"),
        "NaPm": js_value(detail_html, "sNaPm"),
        "bDeliveryLeftRightYN": "N",
        "bQuickPostSortYN": "N",
        "sSortType": "minPrice",
        "sProductFullName": full_title,
        "bPostPriceYN": "N",
        "bBadgeDefaultYN": "N",
        "bWarrantyDefaultYN": "N",
        "nOpenMarketMoreCount": "30",
        "nAffiliateMoreCount": "30",
        "nOverseasShoppingMoreCount": "30",
        "nGeneralAffiliateMoreCount": "3",
        "sRelationMenuType": "",
        "sRelationType": "",
        "bCoupangSortYN": "N",
        "makerCode": js_value(detail_html, "makerCode"),
        "makerName": js_value(detail_html, "makerName"),
    }

    ajax_url = "https://prod.danawa.com/info/ajax/getAllPriceCompareMallList.ajax.php"
    ajax_html = fetch(ajax_url, data=ajax_data, referer=product_url)
    ajax_root = parse_html(ajax_html)

    offers: List[Dict[str, object]] = []
    for div in ajax_root.find_all(lambda n: n.has_class("diff_item")):
        mall_img = div.find_first(lambda n: n.tag == "img" and any(a.has_class("d_mall") for a in n.ancestors()))
        mall = mall_img.attr("alt").strip() if mall_img else ""
        price_node = div.find_first(lambda n: n.tag == "em" and (n.has_class("prc_c") or n.has_class("prc_t")))
        price = parse_int(price_node.text() if price_node else None)
        if not mall or price is None or price <= 0:
            continue

        ship_node = div.find_first(lambda n: n.has_class("ship") or n.has_class("stxt"))
        shipping = ship_node.text() if ship_node else ""
        if "무료" in shipping:
            shipping_fee = 0
            shipping_label = "무료배송"
        else:
            parsed_fee = parse_int(shipping)
            if parsed_fee is None:
                continue  # 배송비 불명확 오퍼는 잘못된 실구매가 계산 방지를 위해 제외
            shipping_fee = parsed_fee
            shipping_label = f"배송비 {shipping_fee:,}원"

        link_node = div.find_first(lambda n: n.tag == "a" and n.has_class("priceCompareBuyLink"))
        buy_url = abs_url(link_node.attr("href")) if link_node else None
        if not buy_url:
            buy_url = product_url

        total_price = price + shipping_fee
        offers.append({
            "mall": mall,
            "price": price,
            "shipping_fee": shipping_fee,
            "shipping_label": shipping_label,
            "total_price": total_price,
            "buy_url": buy_url,
        })

    if not offers:
        raise BotError(f"상품(pcode={pcode})의 쇼핑몰 가격비교 오퍼를 파싱하지 못했습니다.")

    offers.sort(key=lambda o: (int(o["total_price"]), int(o["price"]), str(o["mall"])))
    best = offers[0]
    best["detail_title"] = full_title
    return best


def load_env(path: Path = ENV_PATH) -> Dict[str, str]:
    """Load key=value pairs from .env without relying on external packages."""
    if not path.exists():
        if any(os.environ.get(k) for k in ("KAKAO_REST_API_KEY", "kakao_REST_API_key", "KAKAO_REFRESH_TOKEN")):
            return dict(os.environ)
        raise BotError(f"카카오 인증을 위한 .env 파일을 찾을 수 없습니다: {path}")

    env: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key:
            env[key] = value
    return env


def upsert_env_value(key: str, value: str, path: Path = ENV_PATH) -> None:
    """Update or append a key=value entry in .env."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updated = False
    new_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            existing_key = line.split("=", 1)[0].strip()
            if existing_key == key:
                new_lines.append(f"{key}={value}")
                updated = True
                continue
        new_lines.append(line)
    if not updated:
        new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def get_kakao_credentials(env: Dict[str, str]) -> Dict[str, str]:
    """Read Kakao credentials from parsed .env (or process env fallback)."""
    def _pick(*names: str) -> str:
        for name in names:
            val = env.get(name) or os.environ.get(name, "")
            if val.strip():
                return val.strip()
        return ""

    return {
        "rest_api_key": _pick("kakao_REST_API_key", "KAKAO_REST_API_KEY"),
        "client_secret": _pick("kakao_CLIENT_SECRET", "KAKAO_CLIENT_SECRET"),
        "refresh_token": _pick("KAKAO_REFRESH_TOKEN", "kakao_REFRESH_TOKEN"),
        "access_token": _pick("KAKAO_ACCESS_TOKEN", "kakao_ACCESS_TOKEN"),
    }


def kakao_post(url: str, data: Dict[str, str], *, access_token: Optional[str] = None) -> Dict[str, object]:
    """Send a POST request to Kakao OAuth/API and parse JSON response strictly."""
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", "replace")
        except Exception:
            err_body = ""
        detail = err_body
        if err_body:
            try:
                err_json = json.loads(err_body)
                parts = [
                    str(err_json[k])
                    for k in ("error", "error_code", "error_description", "code", "msg")
                    if k in err_json
                ]
                if parts:
                    detail = " / ".join(parts)
            except Exception:
                pass
        raise BotError(
            f"카카오 API 요청 실패(HTTP {exc.code}): {detail or exc.reason} ({url})"
        ) from exc
    except urllib.error.URLError as exc:
        raise BotError(f"카카오 API 네트워크 연결 실패({exc.reason}): {url}") from exc
    except TimeoutError as exc:
        raise BotError(f"카카오 API 요청 시간 초과({REQUEST_TIMEOUT}s): {url}") from exc
    except OSError as exc:
        raise BotError(f"카카오 API 소켓/네트워크 오류({exc}): {url}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BotError(f"카카오 API 응답 JSON 파싱 실패: {raw[:200]}") from exc
    if not isinstance(parsed, dict):
        raise BotError(f"카카오 API 응답 형식이 올바르지 않습니다: {raw[:200]}")
    return parsed


def refresh_kakao_access_token(creds: Dict[str, str], env_path: Path = ENV_PATH) -> str:
    """Exchange KAKAO_REFRESH_TOKEN for a new Kakao access_token."""
    if not creds["rest_api_key"]:
        raise BotError("카카오톡 알림 발송 실패: .env 파일에 kakao_REST_API_key(또는 KAKAO_REST_API_KEY)가 없습니다.")
    if not creds["refresh_token"]:
        raise BotError(
            "카카오톡 알림 발송 실패: .env 파일에 KAKAO_REFRESH_TOKEN(또는 KAKAO_ACCESS_TOKEN)이 없습니다. "
            "리프레시 토큰을 먼저 발급해 .env에 추가해 주세요."
        )

    payload = {
        "grant_type": "refresh_token",
        "client_id": creds["rest_api_key"],
        "refresh_token": creds["refresh_token"],
    }
    if creds["client_secret"]:
        payload["client_secret"] = creds["client_secret"]

    res = kakao_post(KAKAO_TOKEN_URL, payload)
    access_token = str(res.get("access_token") or "").strip()
    if not access_token:
        raise BotError(f"카카오 액세스 토큰 갱신 실패: 응답에 access_token이 없습니다 ({res})")

    new_refresh = str(res.get("refresh_token") or "").strip()
    if new_refresh and new_refresh != creds["refresh_token"] and env_path.exists():
        upsert_env_value("KAKAO_REFRESH_TOKEN", new_refresh, path=env_path)
    return access_token


def build_kakao_message_payload(item: Dict[str, object]) -> Dict[str, object]:
    """Build Kakao Talk default text template payload within the 200-char limit."""
    header = "[베베숲 20개 특가 알림]\n"
    tail = (
        f"\n가격: {int(item['total_price']):,}원 ({item['shipping_label']})"
        f"\n쇼핑몰: {item['mall']}"
        f"\n리뷰수: {item['review_count']}"
    )
    prefix = "상품명: "
    max_title_len = max(10, KAKAO_TEXT_MAX_LEN - len(header) - len(prefix) - len(tail))
    title = str(item["title"])
    if len(title) > max_title_len:
        title = title[: max_title_len - 1] + "…"
    text = f"{header}{prefix}{title}{tail}"
    link_url = str(item["link"])
    return {
        "object_type": "text",
        "text": text,
        "link": {
            "web_url": link_url,
            "mobile_web_url": link_url,
        },
        "button_title": "구매하러 가기",
    }


def send_kakao_memo(item: Dict[str, object], env_path: Path = ENV_PATH) -> None:
    """Send a KakaoTalk '나에게 보내기' message for the best-priced item."""
    env = load_env(env_path)
    creds = get_kakao_credentials(env)
    template_object = build_kakao_message_payload(item)
    body = {"template_object": json.dumps(template_object, ensure_ascii=False)}

    access_token = creds["access_token"]
    if not access_token:
        access_token = refresh_kakao_access_token(creds, env_path=env_path)

    try:
        res = kakao_post(KAKAO_MEMO_SEND_URL, body, access_token=access_token)
    except BotError as exc:
        # If an existing KAKAO_ACCESS_TOKEN expired (HTTP 401), refresh once when possible.
        if creds["access_token"] and creds["refresh_token"] and "HTTP 401" in str(exc):
            access_token = refresh_kakao_access_token(creds, env_path=env_path)
            res = kakao_post(KAKAO_MEMO_SEND_URL, body, access_token=access_token)
        else:
            raise

    if res.get("result_code") != 0:
        raise BotError(f"카카오톡 '나에게 보내기' 발송 실패: {res}")


def send_test_notification() -> None:
    """Send a mock/test KakaoTalk message to verify notification delivery."""
    test_item = {
        "title": "[테스트 알림] 베베숲 시그니처 물티슈 70매 캡형 (20팩)",
        "total_price": 18900,
        "shipping_label": "무료배송",
        "mall": "G마켓",
        "review_count": "1,420",
        "link": "https://search.danawa.com/dsearch.php?query=" + urllib.parse.quote(QUERY),
    }
    print("[테스트 모드] 카카오톡 '나에게 보내기' 테스트 메시지를 발송합니다...")
    print(f"  - 상품명 : {test_item['title']}")
    print(f"  - 가격   : {int(test_item['total_price']):,}원 ({test_item['shipping_label']})")
    print(f"  - 쇼핑몰 : {test_item['mall']}")
    print(f"  - 리뷰수 : {test_item['review_count']}")
    print(f"  - 링크   : {test_item['link']}")
    send_kakao_memo(test_item)
    print("[카카오톡 알림] 테스트 메시지 발송 완료! 카카오톡을 확인해 주세요.")


def run(argv: Optional[List[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if any(arg in ("--test", "-t", "test") for arg in args):
        send_test_notification()
        return 0

    candidates = search_candidates(QUERY)
    top_candidates = candidates[:CANDIDATE_SCAN_LIMIT]

    resolved: List[Dict[str, object]] = []
    offer_errors: List[str] = []
    for idx, cand in enumerate(top_candidates):
        if idx > 0:
            time.sleep(0.3)
        try:
            best_offer = fetch_lowest_offer(str(cand["pcode"]))
        except BotError as exc:
            offer_errors.append(str(exc))
            continue

        title = str(best_offer.get("detail_title") or cand["title"])
        resolved.append({
            "pcode": cand["pcode"],
            "title": title,
            "total_price": int(best_offer["total_price"]),
            "item_price": int(best_offer["price"]),
            "shipping_label": best_offer["shipping_label"],
            "mall": best_offer["mall"],
            "review_count": cand["review_count"],
            "link": best_offer["buy_url"],
        })

    if len(resolved) < TOP_N:
        detail = "; ".join(offer_errors) if offer_errors else "유효한 오퍼 부족"
        raise BotError(
            f"최저가 상위 {TOP_N}개 상품의 검증된 가격비교 정보를 모두 가져오지 못했습니다 "
            f"(확보: {len(resolved)}개). 원인: {detail}"
        )

    resolved.sort(key=lambda r: (int(r["total_price"]), int(r["item_price"])))
    top3 = resolved[:TOP_N]

    print(f"=== 다나와 '{QUERY}' 최저가 TOP {TOP_N} (광고 제외, 실구매가 기준) ===")
    for rank, item in enumerate(top3, start=1):
        print(f"\n[{rank}위] {item['title']}")
        print(
            f"  - 가격   : {item['total_price']:,}원 "
            f"(상품가 {item['item_price']:,}원 / {item['shipping_label']})"
        )
        print(f"  - 쇼핑몰 : {item['mall']}")
        print(f"  - 리뷰수 : {item['review_count']}")
        print(f"  - 링크   : {item['link']}")

    best_price = int(top3[0]["total_price"])
    print("\n" + "-" * 55)
    print(f"1위 실구매가: {best_price:,}원 / 목표가: {TARGET_PRICE:,}원")
    if best_price <= TARGET_PRICE:
        print("[특가발견] 목표 가격 이하입니다!")
        send_kakao_memo(top3[0])
        print("[카카오톡 알림] '나에게 보내기' 메시지 발송 완료!")
    else:
        print("[유지] 아직 목표 가격보다 비싸요!")
        print("[카카오톡 알림] 목표가 초과로 메시지를 보내지 않았습니다.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except BotError as err:
        print(f"[오류 - 실행 중단] {err}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        print(f"[예기치 않은 오류 - 실행 중단] {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
