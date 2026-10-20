"""将运行必需文件打包为本地 ZIP，不上传或发布。"""

import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    metadata = (root / "metadata.yaml").read_text(encoding="utf-8")
    version = re.search(r"^version:\s*(v[\w.-]+)\s*$", metadata, re.MULTILINE)
    if version is None:
        raise ValueError("metadata.yaml 缺少合法版本号")
    output = root / "dist" / f"astrbot_plugin_cf_challenge-{version[1]}.zip"
    output.parent.mkdir(exist_ok=True)
    files = [
        root / name
        for name in (
            "__init__.py",
            "main.py",
            "metadata.yaml",
            "_conf_schema.json",
            "requirements.txt",
            "README.md",
        )
    ]
    for directory in ("cf_challenge", "tools"):
        files.extend((root / directory).glob("*.py"))
    files.extend(
        path for path in (root / "cf_challenge" / "assets").rglob("*") if path.is_file()
    )
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(files):
            relative = path.relative_to(root).as_posix()
            archive.write(path, f"astrbot_plugin_cf_challenge/{relative}")
    print(output)


if __name__ == "__main__":
    main()
