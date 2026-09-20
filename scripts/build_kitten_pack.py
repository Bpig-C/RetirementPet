"""Build the realistic kitten PetPack from processed assets."""
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VERSION = "0.1.1"
SRC = ROOT / "assets" / "petpack" / f"realistic-kitten-{DEFAULT_VERSION}"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

CANVAS = 512
ACTIONS = [
    ("core.idle", "idle-closed"),
    ("core.work", "work"),
    ("core.rest", "rest-yawn"),
    ("core.eat", "eat"),
    ("core.exercise", "exercise"),
    ("core.meeting", "meeting"),
    ("core.music", "music"),
]
ALL_SEMANTICS = ["core.idle", "core.work", "core.rest", "core.eat",
                 "core.exercise", "core.meeting", "core.music"]


def sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def build_manifest(src: Path = SRC, version: str = DEFAULT_VERSION):
    assets = []
    actions = []
    character_actions = {}
    for semantic, img_name in ACTIONS:
        asset_id = f"asset.{semantic.split('.')[1]}"
        action_id = f"action.{semantic.split('.')[1]}"
        path = f"assets/{img_name}.png"
        data = (src / path).read_bytes()
        assets.append({
            "id": asset_id,
            "path": path,
            "media_type": "image/png",
            "byte_size": len(data),
            "sha256": sha256(data),
            "properties": {"width": CANVAS, "height": CANVAS},
            "rights_ref": "rights.original",
            "source_ref": "source.original",
        })
        actions.append({
            "id": action_id,
            "semantic": semantic,
            "loop_modes": ["repeat"],
            "policy_tags": ["silent", "meeting_safe", "dnd_safe"],
            "audio": None,
            "interrupt": {"policy": "immediate", "max_exit_ms": 0},
            "user_modes": ["auto", "manual", "disabled"],
            "lifecycle": {"loop": {"renderer": {
                "type": "static", "asset": asset_id}}},
        })
        character_actions[semantic] = action_id

    # idle-meow interaction asset
    meow_data = (src / "assets/idle-meow.png").read_bytes()
    assets.append({
        "id": "asset.meow", "path": "assets/idle-meow.png",
        "media_type": "image/png", "byte_size": len(meow_data),
        "sha256": sha256(meow_data),
        "properties": {"width": CANVAS, "height": CANVAS},
        "rights_ref": "rights.original", "source_ref": "source.original",
    })


    m = CANVAS  # 512
    manifest = {
        "schema_version": "1.0",
        "package": {
            "publisher_id": "community.retirementpet",
            "publisher_ref": "community.retirementpet",
            "id": "realistic-kitten",
            "version": version,
            "display_name": {"zh-CN": "真实幼猫", "en": "Realistic Kitten"},
        },
        "publishers": [{
            "id": "community.retirementpet",
            "display_name": "RetirementPet Project",
            "contact": None, "homepage": None,
        }],
        "series": {
            "id": "realistic", "display_name": {"zh-CN": "写实系列"},
        },
        "sources": [{
            "id": "source.original",
            "kind": "original_creation",
            "title": "Realistic kitten original art",
            "creator": "community.retirementpet",
            "locator": None, "accessed_at": None,
        }],
        "rights_declarations": [{
            "id": "rights.original",
            "basis": "original",
            "claimant_ref": "community.retirementpet",
            "attribution": "RetirementPet Project",
            "license": {
                "spdx": None, "custom_name": "RetirementPet Project License",
                "legal_file_ref": "legal.license",
            },
            "scope_claimed": ["personal_use", "redistribution"],
            "notes": None,
        }],
        "legal_files": [{
            "id": "legal.license", "path": "legal/license.txt",
            "media_type": "text/plain", "purpose": "license_text",
            "byte_size": (src / "legal" / "license.txt").stat().st_size,
            "sha256": sha256((src / "legal" / "license.txt").read_bytes()),
        }],
        "assets": assets,
        "actions": actions,
        "characters": [{
            "id": "kitten",
            "display_name": {"zh-CN": "真实幼猫"},
            "thumbnail_asset": "asset.idle",
            "default_variant": None,
            "variants": [],
            "text_profile_refs": [],
            "recommended_profiles": {},
            "rig_bindings": {},
            "rig_contract_ref": None,
            "actions": character_actions,
            "geometry": {
                "logical_canvas": {"width": CANVAS, "height": CANVAS},
                "base_anchor": {"x": m // 2, "y": m - 8},
                "bubble_anchor": {"x": m // 2, "y": 12},
                "content_bounds": {
                    "x": 56, "y": 56, "width": 400, "height": 400},
                "motion_bounds": {
                    "x": 32, "y": 32, "width": 448, "height": 448},
                "hit_regions": [{
                    "shape": "rect", "x": 56, "y": 56,
                    "width": 400, "height": 400}],
                "reference_height": 400,
            },
        }],
        "compatibility": {
            "engine_min": "2.0.0",
            "engine_max_exclusive": "3.0.0",
            "required_capabilities": [
                "renderer.static.v1", "asset.png.v1"],
            "optional_capabilities": [],
        },
    }
    return manifest


def build_pack(output_dir: Path) -> Path:
    src = output_dir.resolve()
    prefix = "realistic-kitten-"
    if not src.name.startswith(prefix):
        raise ValueError(f"source directory must be named {prefix}<version>")
    version = src.name[len(prefix):]
    manifest = build_manifest(src, version)
    # sync assets
    assets_dir = src / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    for action in manifest["actions"]:
        asset_id = action["lifecycle"]["loop"]["renderer"]["asset"]
        asset_entry = next(a for a in manifest["assets"] if a["id"] == asset_id)
        src_file = assets_dir / Path(asset_entry["path"]).name
        if not src_file.exists():
            print(f"WARNING: asset file missing: {src_file}")

    # write manifest
    manifest_path = src / "petpack.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    print(f"manifest written: {manifest_path}")

    # build .petpack
    out_path = ROOT / "assets" / "petpack" / f"realistic-kitten-{version}.petpack"
    import zipfile
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("petpack.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2))
        for f in sorted((SRC / "assets").glob("*.png")):
            zf.writestr(f"assets/{f.name}", f.read_bytes())
        legal = src / "legal" / "license.txt"
        if legal.exists():
            zf.writestr("legal/license.txt", legal.read_bytes())
    print(f"pack built: {out_path} "
          f"({out_path.stat().st_size // 1024} KiB)")
    return out_path


if __name__ == "__main__":
    build_pack(SRC)
