import shutil
from pathlib import Path

from shelf.cli import main
from shelf.index import Index
from shelf.ingest import apply, guess_id, guess_parts, guess_type, scan
from shelf.manifest import Manifest, Wanted
from tests.conftest import make_pdf, poppler


def test_guess_type_from_names() -> None:
    assert guess_type("rm0433-stm32h7.pdf", "") == "reference-manual"
    assert guess_type("es0392-errata.pdf", "device errata") == "errata"
    assert guess_type("data sheets/pcm3168a.pdf", "PCM3168A ... datasheet (Rev. A)") == "datasheet"
    assert guess_type("data sheets/ICs/lm555.pdf", "LM555 Timer datasheet") == "datasheet"
    assert guess_type("data sheets/cd4046b.pdf", "") == "datasheet"  # directory hint
    assert guess_type("papers/echoplex.pdf", "") == "paper"
    assert guess_type("patch_init_schematic.pdf", "ES_Daisy_Patch_SM_Init_Rev1.sch") == "schematic"
    assert guess_type("i2c-spec-UM10204.pdf", "UM10204 I2C-bus specification and user manual") == "standard"
    assert guess_type("um1472-discovery-kit.pdf", "Discovery kit - User manual") == "user-manual"
    assert guess_type("bluetooth_cr_UG.pdf", "Bluetooth Command Reference User’s Guide") == "user-manual"
    assert guess_type("SerLCD_ApplicationNote_r1.pdf", "") == "app-note"
    assert guess_type("data sheets/GDEH0213B72.pdf", "HINK-E0213A50 Spec.doc") == "datasheet"
    assert guess_type("data sheets/PCF8574.pdf", "Remote 8-bit I/O expander for I2C-bus") == "datasheet"
    assert guess_type("random.pdf", "") == "other"


def test_guess_id_prefers_document_numbers() -> None:
    assert guess_id("rm0433-stm32h742-very-long-name.pdf", "") == "rm0433"
    assert guess_id("es0392-stm32h742xig.pdf", "") == "es0392"
    assert guess_id("ARM/cortexm4-generic-user-guide-DUI0553.pdf", "") == "dui0553"
    assert guess_id("AS358-358A-358B-1512593.pdf", "") == "as358-358a-358b"
    assert guess_id("Teensy4.1 Pins.pdf", "") == "teensy4-1-pins"
    assert guess_id("IMXRT1060RM_rev2.pdf", "") == "imxrt1060rm"
    assert guess_id("IMXRT1060CEC_rev0_1.pdf", "") == "imxrt1060cec"
    assert guess_id("ICs/MCP4725_2009.pdf", "") == "mcp4725"


def test_guess_parts_is_conservative() -> None:
    assert "STM32H750XB" in guess_parts("es0392-stm32h742xig-stm32h750xb.pdf", "")
    assert "ES0392" not in guess_parts("es0392-stm32h742xig-stm32h750xb.pdf", "")
    assert guess_parts("cd4046b.pdf", "CD4046B TYPES datasheet (Rev. B)") == ["CD4046B"]
    assert guess_parts("papers/echoplex.pdf", "A Digital Model of the Echoplex") == []


@poppler
def test_scan_and_apply(shelf_root: Path, sample_pdf: Path) -> None:
    (shelf_root / "data sheets").mkdir()
    shutil.copy(sample_pdf, shelf_root / "data sheets" / "rm0433-test.pdf")
    other = shelf_root / "papers"
    other.mkdir()
    (other / "paper.pdf").write_bytes(make_pdf(["A digital model of tape delay"]))
    (other / "paper2.pdf").write_bytes(make_pdf(["A digital model of tape delay"]))  # sorts after paper.pdf

    manifest = Manifest.load(shelf_root / "shelf.json")
    manifest.add_wanted(Wanted("rm0433", "RM0433", why="test"))
    plan = scan(shelf_root, manifest)
    assert [c.id for c in plan.new] == ["rm0433", "paper"], "a wanted id is fulfilled, not suffixed"
    assert plan.new[0].type == "reference-manual"
    assert plan.new[1].type == "paper"
    assert plan.duplicates == [("papers/paper2.pdf", "paper")]

    idx = Index(shelf_root / ".shelf" / "catalog.db")
    apply(plan, manifest, shelf_root, idx)
    manifest.save(shelf_root / "shelf.json")
    assert manifest.wanted == []
    assert manifest.get("rm0433").text_layer != "unknown"
    assert manifest.get("rm0433").notes.startswith("ingested")
    assert idx.search("fifo", manifest)[0].doc_id == "rm0433"

    # Move a file: rescan detects relocation by hash and repairs the path.
    (shelf_root / "data sheets" / "rm0433-test.pdf").rename(shelf_root / "rm0433.pdf")
    plan2 = scan(shelf_root, manifest)
    assert plan2.new == []
    assert [(d.id, rel) for d, rel in plan2.relocated] == [("rm0433", "rm0433.pdf")]
    apply(plan2, manifest, shelf_root, idx)
    assert manifest.get("rm0433").file == "rm0433.pdf"
    idx.close()


@poppler
def test_cli_ingest_dry_run_then_apply(shelf_root: Path, sample_pdf: Path, capsys) -> None:
    shutil.copy(sample_pdf, shelf_root / "ds-sample.pdf")
    root = str(shelf_root)
    assert main(["--root", root, "ingest"]) == 0
    out = capsys.readouterr().out
    assert "ds-sample" in out and "--apply" in out
    assert Manifest.load(shelf_root / "shelf.json").documents == []

    assert main(["--root", root, "ingest", "--apply"]) == 0
    assert [d.id for d in Manifest.load(shelf_root / "shelf.json").documents] == ["ds-sample"]
    assert main(["--root", root, "ingest"]) == 0
    assert "nothing to ingest" in capsys.readouterr().out
