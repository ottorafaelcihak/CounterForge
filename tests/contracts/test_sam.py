import base64
from io import BytesIO
from uuid import UUID

import pytest
from PIL import Image

WIRE = "<0f>7<|box;x1=0;y1=0;x2=2;y2=1;w=3;h=2|><|mask;x=0;y=0;data=2,3,~!!!!.!0^zlTde]:)]`W|>\n"


def events(text=WIRE, *, terminal=True):
    lane = {"item_id": "msg-fixture", "output_index": 0, "content_index": 0}
    result = [
        {"type": "response.output_text.delta", **lane, "delta": text},
        {"type": "response.output_text.done", **lane, "text": text},
    ]
    if terminal:
        result.append(
            {"type": "response.completed", "response": {"id": "fixture", "status": "completed"}}
        )
    return result


async def test_official_parser_expands_box_local_mask_without_resizing_source():
    from counterforge_meta.sam import normalize_events

    result = await normalize_events(events(), width=3, height=2)
    assert result.outcome == "COMPLETED"
    assert len(result.objects) == 1
    obj = result.objects[0]
    assert obj.object_id == "7"
    assert obj.bbox_xyxy == (0, 0, 3, 2)
    with Image.open(BytesIO(obj.png_bytes)) as mask:
        assert mask.size == (3, 2)
        assert list(mask.get_flattened_data()) == [0, 0, 255, 255, 0, 255]
    assert base64.b64encode(obj.png_bytes).decode() not in repr(result)


@pytest.mark.parametrize(
    "records,width,height,expected",
    [
        (events(terminal=False), 3, 2, "INCOMPLETE"),
        (events(""), 3, 2, "INCOMPLETE"),
        (events(WIRE.replace("~!!!!.!0^zlTde]:)]`W", "!invalid")), 3, 2, "INCOMPLETE"),
        (events(), 2, 2, "INCOMPLETE"),
        (events(WIRE + WIRE), 3, 2, "INCOMPLETE"),
        ([{"type": "response.refusal.done", "refusal": "no"}], 3, 2, "REFUSED"),
        (events(), 999999, 999999, "INCOMPLETE"),
    ],
)
async def test_invalid_truncated_refused_empty_or_oversized_never_returns_masks(
    records, width, height, expected
):
    from counterforge_meta.sam import normalize_events

    result = await normalize_events(records, width=width, height=height)
    assert result.outcome == expected
    assert result.objects == ()
    assert result.diagnostics


async def test_completed_with_parser_error_diagnostics_is_invalid():
    from counterforge_meta.sam import normalize_events

    result = await normalize_events(
        events(WIRE + "<0f>8<|box;x1=NaN;y1=0;x2=2;y2=1;w=3;h=2|>\n"), width=3, height=2
    )
    assert result.outcome == "INCOMPLETE"
    assert not result.objects


async def test_segmenter_persists_immutable_pngs_via_writer(vtc_payload):
    from counterforge_contracts.providers import ProviderResult
    from counterforge_meta.sam import SamSegmenter
    from counterforge_meta.transport import CallResponse

    output = BytesIO()
    Image.new("RGB", (3, 2)).save(output, format="PNG")
    stored = []

    async def writer(object_id, png_bytes):
        stored.append((object_id, png_bytes))
        return UUID(int=1)

    class Transport:
        async def sam(self, **kwargs):
            return CallResponse(
                "COMPLETED",
                ProviderResult(provider="META", model="sam-3.1", latency_ms=0),
                events=tuple(events()),
            )

    result = await SamSegmenter(transport=Transport(), mask_writer=writer).segment(
        output.getvalue(), "blue container"
    )
    assert result.outcome == "COMPLETED"
    assert result.objects[0].mask_asset_id == UUID(int=1)
    assert stored[0][0] == "7"


async def test_empty_binary_mask_is_rejected_after_official_decode():
    from counterforge_meta.sam import normalize_events

    # Canonical 3x2 all-zero one-bit fixture emitted by the installed official codec.
    empty = WIRE.replace("~!!!!.!0^zlTde]:)]`W", "!!!!!&!!!!!")
    result = await normalize_events(events(empty), width=3, height=2)
    assert result.outcome == "INCOMPLETE"
    assert result.objects == ()
    assert result.diagnostics == ("SAM_MASK_BINARY_NONEMPTY",)


async def test_more_than_sixteen_official_instances_is_rejected():
    from counterforge_meta.sam import normalize_events

    text = "".join(WIRE.replace("<0f>7", f"<0f>{index}") for index in range(17))
    result = await normalize_events(events(text), width=3, height=2)
    assert result.outcome == "INCOMPLETE"
    assert result.objects == ()
    assert result.diagnostics == ("SAM_OBJECT_LIMIT",)


async def test_mask_is_padded_to_source_dimensions_without_resizing():
    from counterforge_meta.sam import normalize_events

    result = await normalize_events(events(), width=5, height=4)
    assert result.outcome == "COMPLETED"
    with Image.open(BytesIO(result.objects[0].png_bytes)) as image:
        assert image.size == (5, 4)
        assert list(image.get_flattened_data()) == [
            0,
            0,
            255,
            0,
            0,
            255,
            0,
            255,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ]


async def test_hostile_eager_mask_allocation_is_contained_by_child_limit():
    from counterforge_meta.sam import normalize_events

    hostile = WIRE.replace("data=2,3,", "data=1000000,1000000,")
    result = await normalize_events(events(hostile), width=3, height=2)
    assert result.outcome == "INCOMPLETE"
    assert result.objects == ()
