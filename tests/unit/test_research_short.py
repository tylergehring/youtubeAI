from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from PIL import ImageColor

from core.exceptions import InvalidInputError, ProviderError, QualityCheckError
from core.explainer import BondScenario, bond_price, calculation_context, resolve_text
from core.hashing import file_sha256
from core.models.content import Transcript, TranscriptSegment
from core.storage import read_model, write_model
from core.youtube_transcript import YouTubeSource, fetch_transcript, video_id_from_url
from pipeline.explainer import load_brief, preflight
from rendering.explainer import CORAL, TEAL, ExplainerGraphics

BRIEF = Path(__file__).resolve().parents[2] / "examples" / "explainers" / "bond_price_risk.yaml"


@pytest.mark.parametrize(
    "value",
    [
        "5GRH9EeH-DQ",
        "https://www.youtube.com/watch?v=5GRH9EeH-DQ&t=30",
        "https://youtu.be/5GRH9EeH-DQ",
        "https://www.youtube.com/shorts/5GRH9EeH-DQ",
    ],
)
def test_youtube_id(value: str) -> None:
    assert video_id_from_url(value) == "5GRH9EeH-DQ"


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.example/watch?v=5GRH9EeH-DQ",
        "https://youtube.com.evil.example/watch?v=5GRH9EeH-DQ",
        "https://user@youtube.com/watch?v=5GRH9EeH-DQ",
        "../5GRH9EeH-DQ",
        "https://youtube.com/watch?v=too-short",
    ],
)
def test_invalid_youtube_source(value: str) -> None:
    with pytest.raises(InvalidInputError):
        video_id_from_url(value)


def test_bond_cashflows_and_yield_sensitivity() -> None:
    scenario = BondScenario()
    assert bond_price(scenario, 30, Decimal("4")) == pytest.approx(Decimal("1000"))
    assert round(bond_price(scenario, 2, Decimal("5")), 2) == Decimal("981.19")
    assert round(bond_price(scenario, 30, Decimal("5")), 2) == Decimal("845.46")
    assert bond_price(scenario, 2, Decimal("0")) == Decimal("1080")
    assert bond_price(scenario, 30, Decimal("3")) > Decimal("1000")
    context = calculation_context(scenario)
    assert context["long_price"] == "$845"
    assert context["short_loss_pct"] == "1.9%"
    assert context["long_loss_pct"] == "15.5%"
    assert resolve_text("Price: {long_price}", context) == "Price: $845"
    with pytest.raises(InvalidInputError, match="Unknown calculation"):
        resolve_text("{invented_statistic}", context)


def test_transcript_intake_records_hashes_and_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = pytest.importorskip("youtube_transcript_api")

    class Fetched(list):
        language_code = "en"
        is_generated = True

    monkeypatch.setattr(
        package.YouTubeTranscriptApi,
        "fetch",
        lambda self, vid, languages: Fetched(
            [SimpleNamespace(text="A test research statement.", start=0.5, duration=2.0)]
        ),
    )
    source = fetch_transcript("5GRH9EeH-DQ", tmp_path, ["en"])
    assert source.is_generated
    assert source.transcript_sha256 == file_sha256(tmp_path / "transcript.json")
    saved = read_model(tmp_path / "transcript.json", Transcript)
    assert saved.segments[0].end_s == 2.5
    with pytest.raises(InvalidInputError, match="not empty"):
        fetch_transcript("5GRH9EeH-DQ", tmp_path, ["en"])


def test_unavailable_transcript_is_an_explicit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = pytest.importorskip("youtube_transcript_api")

    def fail(*args, **kwargs):
        raise package.TranscriptsDisabled("5GRH9EeH-DQ")

    monkeypatch.setattr(package.YouTubeTranscriptApi, "fetch", fail)
    with pytest.raises(ProviderError, match="No transcript was invented"):
        fetch_transcript("5GRH9EeH-DQ", tmp_path, ["en"])
    assert not list(tmp_path.iterdir())


def research_fixture(directory: Path, text: str = "An unrelated financial research lead.") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    write_model(
        directory / "transcript.json",
        Transcript(format="txt", segments=[TranscriptSegment(text=text)]),
    )
    write_model(
        directory / "source.json",
        YouTubeSource(
            video_id="5GRH9EeH-DQ",
            reference_url="https://www.youtube.com/watch?v=5GRH9EeH-DQ",
            language="en",
            is_generated=False,
            transcript_sha256=file_sha256(directory / "transcript.json"),
        ),
    )
    return directory


def test_brief_evidence_and_every_visual_fit(tmp_path: Path) -> None:
    brief = load_brief(BRIEF)
    overlap, sources = preflight(brief, BRIEF, research_fixture(tmp_path))
    assert overlap == 0
    assert len(sources) == 2
    assert sum(len(scene.narration.split()) for scene in brief.scenes) <= 105
    assert all(len(scene.narration.split()) <= 27 for scene in brief.scenes)
    assert all(len(scene.heading.split()) <= 8 for scene in brief.scenes)
    assert all(len(scene.supporting_text.split()) <= 7 for scene in brief.scenes)
    bad = brief.model_copy(update={"source_video_id": "AAAAAAAAAAA"})
    with pytest.raises(InvalidInputError, match="does not match"):
        preflight(bad, BRIEF, tmp_path)
    (tmp_path / "transcript.json").write_text("changed", encoding="utf-8")
    with pytest.raises(InvalidInputError, match="changed"):
        preflight(brief, BRIEF, tmp_path)


def test_narration_is_not_burned_into_visual_frames() -> None:
    brief = load_brief(BRIEF)
    scene = brief.scenes[0]
    graphics = ExplainerGraphics(brief.scenario)
    original = graphics.frame(scene, 0, len(brief.scenes), 0.5)
    alternate = graphics.frame(
        scene.model_copy(update={"narration": "A different sentence, spoken but not displayed."}),
        0,
        len(brief.scenes),
        0.5,
    )
    assert original.tobytes() == alternate.tobytes()


def test_background_graphics_are_translucent_without_changing_default_frames() -> None:
    brief = load_brief(BRIEF)
    graphics = ExplainerGraphics(brief.scenario)
    scene = brief.scenes[0]
    standard = graphics.frame(scene, 0, len(brief.scenes), 0.5)
    overlay = graphics.frame(scene, 0, len(brief.scenes), 0.5, background_video=True)
    assert standard.mode == "RGB"
    assert overlay.mode == "RGBA"
    assert overlay.getpixel((500, 500)) == (13, 23, 36, 185)


def test_comparison_chart_reveals_bars_early() -> None:
    brief = load_brief(BRIEF)
    scene = next(scene for scene in brief.scenes if scene.visual == "comparison")
    frame = ExplainerGraphics(brief.scenario).frame(scene, 2, len(brief.scenes), 0.15)
    assert frame.getpixel((300, 820)) == ImageColor.getrgb(TEAL)


def test_opening_reveals_sale_price_after_face_value() -> None:
    brief = load_brief(BRIEF)
    scene = next(scene for scene in brief.scenes if scene.visual == "hook")
    graphics = ExplainerGraphics(brief.scenario)
    early = graphics.frame(scene, 0, len(brief.scenes), 0.0)
    later = graphics.frame(scene, 0, len(brief.scenes), 0.5)
    color = ImageColor.getrgb(CORAL)
    sale_area = (600, 750, 980, 1020)
    early_colors = early.crop(sale_area).getcolors(103_000) or []
    later_colors = later.crop(sale_area).getcolors(103_000) or []
    assert sum(count for count, pixel in early_colors if pixel == color) == 0
    assert sum(count for count, pixel in later_colors if pixel == color) > 0


def test_rewritten_source_is_not_a_substitute_for_original_script(tmp_path: Path) -> None:
    brief = load_brief(BRIEF)
    context = calculation_context(brief.scenario)
    copied = " ".join(resolve_text(s.narration, context) for s in brief.scenes)
    research = research_fixture(tmp_path, copied)
    with pytest.raises(QualityCheckError, match="overlap"):
        preflight(brief, BRIEF, research)


def test_unknown_citations_and_unlabeled_hypotheticals_are_rejected(tmp_path: Path) -> None:
    data = yaml.safe_load(BRIEF.read_text())
    data["scenes"][0]["evidence_ids"] = ["invented"]
    broken = tmp_path / "broken.yaml"
    broken.write_text(yaml.safe_dump(data))
    with pytest.raises(InvalidInputError, match="known evidence"):
        load_brief(broken)
    data["scenes"][0]["evidence_ids"] = ["treasury_pricing"]
    data["disclosure"] = "A long disclaimer that does not label any of the assumed yields."
    broken.write_text(yaml.safe_dump(data))
    with pytest.raises(InvalidInputError, match="hypothetical"):
        load_brief(broken)


def test_future_briefs_reject_dense_screen_text_and_fast_scripts(tmp_path: Path) -> None:
    data = yaml.safe_load(BRIEF.read_text(encoding="utf-8"))
    data["scenes"][0]["supporting_text"] = " ".join(["extra"] * 8)
    broken = tmp_path / "dense.yaml"
    broken.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(InvalidInputError, match="supporting_text"):
        load_brief(broken)
    data["scenes"][0]["supporting_text"] = "A small price example."
    data["scenes"][0]["narration"] = " ".join(["quickly"] * 29)
    broken.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(InvalidInputError, match="28 words"):
        load_brief(broken)
    data["scenes"][0]["narration"] = "A short sentence that is safely under the per-scene limit."
    for scene in data["scenes"]:
        scene["narration"] = " ".join(["plain"] * 26)
    broken.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(InvalidInputError, match="105 words"):
        load_brief(broken)


def test_changed_evidence_is_rejected(tmp_path: Path) -> None:
    brief = load_brief(BRIEF)
    brief.evidence[0].snapshot_sha256 = "0" * 64
    with pytest.raises(InvalidInputError, match="changed evidence"):
        preflight(brief, BRIEF, research_fixture(tmp_path))


def test_failed_run_is_persisted_and_inputs_are_immutable(tmp_path: Path) -> None:
    from core.storage import read_json
    from pipeline.explainer import _run_record

    manifest = tmp_path / "manifest.json"
    with pytest.raises(QualityCheckError), _run_record(manifest, "original"):
        raise QualityCheckError("A test failure")
    record = read_json(manifest)
    assert record["status"] == "failed" and "test failure" in record["error"]
    with pytest.raises(InvalidInputError, match="different inputs"), _run_record(manifest, "new"):
        pass
    assert read_json(manifest) == record
