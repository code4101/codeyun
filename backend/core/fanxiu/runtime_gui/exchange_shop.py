from __future__ import annotations

"""Pure OCR-to-row alignment for the common activity exchange shop."""

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Sequence

from backend.core.fanxiu.runtime_gui.text import normalize_ocr_name


_UI_OWNERSHIP_SUFFIXES = ("已认主", "已拥有", "已激活")

# 实测几何（2026-09-16，#519 兑换宝阁第二屏起）：商品行标注框高 160、行距 180，
# 而“所需：<价格>”固定渲染在名称下方约 58px，其数字行因此可能落在标注框下缘
# 之外 1~37px（装备玄铁宝匣 D100 即价格行 663~698 对行框 508~668）。按严格包含
# 判定价格会把“名字在框内、价格在框外”的正常行判成不存在，形成假阴性。
# 价格因此按行框下缘放宽 PRICE_ROW_TOLERANCE_RATIO 个行高再判定；名称仍严格包含，
# 保证“名称→行”的归属不变，容差远小于行距也不会跨到相邻行。
PRICE_ROW_TOLERANCE_RATIO = 0.25


def _normalize_product_name(value: Any) -> str:
    normalized = normalize_ocr_name(value)
    for suffix in _UI_OWNERSHIP_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[:-len(suffix)]
    return normalized


@dataclass(frozen=True)
class ExchangeShopItemTarget:
    """One uniquely aligned product-name click target."""

    x: float
    y: float
    row_index: int
    observed_name: str
    current_unit_price: int | None


def exchange_scroll_direction(lines, *, items, goods_id):
    """Choose direction from unique fuzzy title anchors in native Runtime order.

    None means no usable anchors; 'visible' means scrolling cannot fix the
    unresolved identity inside the current window. Never mistake missing OCR
    for being below the top of a list.
    """
    from backend.core.fanxiu.runtime_gui import ocr_name_similarity
    rows=list(items)
    target=next(i for i,r in enumerate(rows) if r['goods_id']==goods_id)
    anchors=[]
    for line in lines:
        ranked=sorted(((ocr_name_similarity(r['name'],line.get('text','')),i)
                       for i,r in enumerate(rows)),reverse=True)
        if ranked and ranked[0][0]>=0.8 and (len(ranked)==1 or ranked[0][0]-ranked[1][0]>=0.12):
            anchors.append((float(line['y']),ranked[0][1]))
    if not anchors: return None
    ordered=[i for _,i in sorted(anchors)]
    if ordered!=sorted(ordered): return None
    if target<min(ordered): return 'up'
    if target>max(ordered): return 'down'
    return 'visible'


def resolve_ordered_exchange_candidate(
    lines: Iterable[Mapping[str, Any]], *, items: Sequence[Mapping[str, Any]],
    goods_id: int, product_list_box: Mapping[str, Any], row_height: float,
) -> ExchangeShopItemTarget:
    """Infer a candidate from three unique ordered neighbors, never authorize a buy.

    Fits current pixel positions against current Runtime order, so scrolling
    invalidates the observation automatically. At most one row of extrapolation
    is allowed. Pooled UI objects are deliberately not treated as visible rows.
    The caller MUST verify goods_id in the opened Runtime purchase dialog.
    """
    from collections import Counter
    from backend.core.fanxiu.runtime_gui import ocr_name_similarity
    rows = list(items)
    names = Counter(_normalize_product_name(r['name']) for r in rows)
    tokens = list(lines)
    anchors = []
    target = [i for i, r in enumerate(rows) if int(r['goods_id']) == int(goods_id)]
    if len(target) != 1:
        raise RuntimeError('Runtime 商品身份不唯一')
    for i, row in enumerate(rows):
        name = _normalize_product_name(row['name'])
        hits = [t for t in tokens if _contained(t, product_list_box)
                and ocr_name_similarity(row['name'],str(t.get('text',''))) >= 0.8
                and all(ocr_name_similarity(other['name'],str(t.get('text',''))) <=
                        ocr_name_similarity(row['name'],str(t.get('text',''))) - 0.12
                        for j,other in enumerate(rows) if j!=i)]
        if names[name] == 1 and len(hits) == 1:
            t = hits[0]
            anchors.append((i, float(t['y']) + float(t['h'])/2,
                            float(t['x']) + float(t['w'])/2))
    if len(anchors) < 3:
        raise RuntimeError('可见有序唯一锚点不足三个')
    mean_i = sum(a[0] for a in anchors)/len(anchors)
    mean_y = sum(a[1] for a in anchors)/len(anchors)
    denominator = sum((a[0]-mean_i)**2 for a in anchors)
    pitch = sum((a[0]-mean_i)*(a[1]-mean_y) for a in anchors)/denominator
    intercept = mean_y-pitch*mean_i
    if not row_height <= pitch <= row_height*1.3:
        raise RuntimeError('当前行距不符合标注布局')
    if max(abs(y-(intercept+pitch*i)) for i,y,_ in anchors) > 6:
        raise RuntimeError('当前画面顺序与 Runtime 列表不一致')
    index = target[0]
    if not min(a[0] for a in anchors)-1 <= index <= max(a[0] for a in anchors)+1:
        raise RuntimeError('目标超出有序锚点的可靠范围')
    x = sum(a[2] for a in anchors)/len(anchors)
    y = intercept+pitch*index
    left,top,right,bottom = _bounds(product_list_box)
    if not left < x < right or not top+row_height/2 < y < bottom-row_height/2:
        raise RuntimeError('目标候选接近列表裁切边界')
    return ExchangeShopItemTarget(x,y,index+1,'runtime_order_candidate',None)


def _number(value: Any) -> int | None:
    text = normalize_ocr_name(value)
    if not re.fullmatch(r"\d+", text):
        return None
    return int(text)


def _bounds(box: Mapping[str, Any]) -> tuple[float, float, float, float]:
    left = float(box["x"])
    top = float(box["y"])
    return left, top, left + float(box["w"]), top + float(box["h"])


def _line_bounds(line: Mapping[str, Any]) -> tuple[float, float, float, float]:
    left = float(line.get("x") or 0)
    top = float(line.get("y") or 0)
    return left, top, left + float(line.get("w") or 0), top + float(line.get("h") or 0)


def _contained(line: Mapping[str, Any], box: Mapping[str, Any]) -> bool:
    line_left, line_top, line_right, line_bottom = _line_bounds(line)
    left, top, right, bottom = _bounds(box)
    return (
        left <= line_left
        and line_right <= right
        and top <= line_top
        and line_bottom <= bottom
    )


def _price_belongs_to_row(line: Mapping[str, Any], box: Mapping[str, Any]) -> bool:
    """价格行按行框下缘容差判定归属；名称仍用严格包含。

    价格数字行允许压在标注框下缘之外（见 ``PRICE_ROW_TOLERANCE_RATIO``），
    但上缘不得高于行框顶部、左右仍在行框内，避免跨行误取相邻商品的价格。
    """

    line_left, line_top, line_right, _line_bottom = _line_bounds(line)
    left, top, right, bottom = _bounds(box)
    if not (left <= line_left and line_right <= right):
        return False
    return top <= line_top <= bottom + (bottom - top) * PRICE_ROW_TOLERANCE_RATIO


def resolve_exchange_shop_item(
    lines: Iterable[Mapping[str, Any]],
    *,
    product_list_box: Mapping[str, Any],
    product_row_boxes: Sequence[Mapping[str, Any]],
    expected_name: str,
    expected_unit_price: int | None = None,
    current_price_right_ratio: float = 0.5,
) -> ExchangeShopItemTarget:
    """Resolve one exact product row, failing closed on missing or ambiguous evidence.

    Product names are compared after OCR-name normalization.  When duplicate
    names are visible, the expected unit price is compared only with the
    left-side numeric value below that row's name.  A crossed-out original
    price rendered in the right half of the row is never accepted as current.
    """

    grouped_lines = list(lines)
    expected_key = _normalize_product_name(expected_name)
    if not expected_key:
        raise RuntimeError("兑换商品名不能为空")
    if not product_row_boxes:
        raise RuntimeError("兑换商品行坐标为空")

    candidates: list[tuple[Mapping[str, Any], int, Mapping[str, Any]]] = []
    for line in grouped_lines:
        if _normalize_product_name(line.get("text")) != expected_key:
            continue
        if not _contained(line, product_list_box):
            continue
        _, line_top, _, line_bottom = _line_bounds(line)
        center_y = (line_top + line_bottom) / 2
        matching_rows = [
            (index, row_box)
            for index, row_box in enumerate(product_row_boxes, start=1)
            if _bounds(row_box)[1] <= center_y <= _bounds(row_box)[3]
        ]
        if len(matching_rows) == 1:
            row_index, row_box = matching_rows[0]
            candidates.append((line, row_index, row_box))

    aligned: list[tuple[Mapping[str, Any], int, int | None]] = []
    for line, row_index, row_box in candidates:
        current_price: int | None = None
        if expected_unit_price is not None:
            name_left, name_top, name_right, name_bottom = _line_bounds(line)
            del name_left, name_top, name_right
            row_left, _, row_right, _ = _bounds(row_box)
            row_midpoint = row_left + (row_right-row_left)*current_price_right_ratio
            price_lines = []
            for price_line in grouped_lines:
                price = _number(price_line.get("text"))
                price_left, price_top, price_right, _ = _line_bounds(price_line)
                if (
                    price is not None
                    and _price_belongs_to_row(price_line, row_box)
                    and price_top > name_bottom
                    and (price_left + price_right) / 2 <= row_midpoint
                ):
                    price_lines.append((price_left, price))
            matching_prices = [
                (price_left, price)
                for price_left, price in price_lines
                if price == int(expected_unit_price)
            ]
            if not matching_prices:
                continue
            # Item-stack counts and the purple currency glyph can also be
            # OCR'd as small numbers before the actual price.  Match the
            # Runtime price first, then take its rightmost occurrence inside
            # the left/current-price half.  Crossed-out originals remain
            # excluded by the midpoint boundary above.
            current_price = max(matching_prices, key=lambda item: item[0])[1]
        aligned.append((line, row_index, current_price))

    if len(aligned) != 1:
        raise RuntimeError(
            f"兑换商品 {expected_name} 唯一命中数为 {len(aligned)}"
        )

    line, row_index, current_price = aligned[0]
    left, top, right, bottom = _line_bounds(line)
    return ExchangeShopItemTarget(
        x=(left + right) / 2,
        y=(top + bottom) / 2,
        row_index=row_index,
        observed_name=str(line.get("text") or ""),
        current_unit_price=current_price,
    )
