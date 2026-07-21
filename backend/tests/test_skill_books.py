from pathlib import Path

from backend.api import skill_books


def _write_skill(root: Path, name: str, body: str, *, description: str = "") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        f'---\nname: "{name}"\ndescription: "{description}"\n---\n\n# {name}\n\n{body}\n',
        encoding="utf-8",
    )
    return skill_path


def test_skill_book_catalog_discovers_main_and_reference_chapters(tmp_path):
    _write_skill(tmp_path, "前端UI规范", "正文", description="界面规范")
    references = tmp_path / "前端UI规范" / "references"
    references.mkdir()
    (references / "案例.md").write_text("# 视觉案例\n\n内容", encoding="utf-8")

    catalog, lookup = skill_books._scan_skill_book(tmp_path)

    assert catalog.skill_count == 1
    assert catalog.chapter_count == 2
    assert catalog.skills[0].name == "前端UI规范"
    assert catalog.skills[0].description == "界面规范"
    assert [chapter.title for chapter in catalog.skills[0].chapters] == ["前端UI规范", "视觉案例"]
    assert set(lookup) == {chapter.id for chapter in catalog.skills[0].chapters}


def test_skill_book_catalog_and_content_reflect_file_changes_without_snapshot(tmp_path):
    skill_path = _write_skill(tmp_path, "动态技能", "第一版")
    first_catalog, first_lookup = skill_books._scan_skill_book(tmp_path)
    chapter_id = first_catalog.skills[0].chapters[0].id
    first_path = first_lookup[chapter_id][1]
    assert "第一版" in skill_books._split_frontmatter(skill_books._read_markdown(first_path))[1]

    skill_path.write_text(
        '---\nname: "动态技能"\ndescription: "实时内容"\n---\n\n# 动态技能\n\n第二版已经更新\n',
        encoding="utf-8",
    )

    second_catalog, second_lookup = skill_books._scan_skill_book(tmp_path)
    second_path = second_lookup[chapter_id][1]
    second_body = skill_books._split_frontmatter(skill_books._read_markdown(second_path))[1]

    assert second_catalog.revision != first_catalog.revision
    assert second_catalog.skills[0].description == "实时内容"
    assert "第二版已经更新" in second_body
    assert "第一版" not in second_body
