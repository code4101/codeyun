from __future__ import annotations

import json
from io import BytesIO
import time
from zipfile import ZipFile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from backend.core.settings import get_settings


OPEN_SCORE_PARSER_VERSION = 2


@dataclass(frozen=True)
class OpenScoreWork:
    id: str
    title: str
    composer: str
    level: str
    form: str
    instrumentation: list[str]
    description: str
    listen_focus: list[str]
    source_name: str
    license_label: str
    source_url: str
    midi_url: str
    pdf_url: str
    part_names: list[str]
    part_roles: list[str]
    study_kind: str = "完整曲目"


OPEN_SCORE_WORKS: tuple[OpenScoreWork, ...] = (
    OpenScoreWork(
        id="bach-violin-concerto-e-major",
        title="Violin Concerto in E major, BWV 1042",
        composer="J. S. Bach",
        level="独奏主角",
        form="小提琴协奏曲",
        instrumentation=["Solo Violin", "Violin I", "Violin II", "Viola", "Cello"],
        description="真实公版协奏曲 MIDI。独奏小提琴是明确主角，弦乐队负责托底、呼应和推进，比普通四重奏更接近“一个乐器独奏，整组乐器伴奏”的听法。",
        listen_focus=["独奏小提琴主题", "伴奏弦乐节奏型", "独奏与齐奏交替", "大提琴低音线"],
        source_name="Mutopia Project",
        license_label="Public Domain",
        source_url="https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=29",
        midi_url="https://www.mutopiaproject.org/ftp/BachJS/BWV1042/concerto-in-e-major/concerto-in-e-major-mids.zip",
        pdf_url="https://www.mutopiaproject.org/ftp/BachJS/BWV1042/concerto-in-e-major/concerto-in-e-major-a4-pdfs.zip",
        part_names=["Solo Violin", "Violin I", "Violin II", "Viola", "Cello"],
        part_roles=["独奏主角", "弦乐高声部", "弦乐应答", "中声部", "低音根基"],
        study_kind="独奏主角",
    ),
    OpenScoreWork(
        id="bach-double-violin-concerto",
        title="Concerto in D minor for two violins, BWV 1043",
        composer="J. S. Bach",
        level="双独奏主角",
        form="双小提琴协奏曲",
        instrumentation=["Solo Violin I", "Solo Violin II", "Violin I", "Violin II", "Viola", "Cello"],
        description="真实公版双小提琴协奏曲 MIDI。两个独奏小提琴互相追逐、模仿和交接主题，伴奏弦乐维持和声与节奏，适合听“两个主角如何对话”。",
        listen_focus=["第一独奏小提琴", "第二独奏小提琴", "双主角模仿", "伴奏弦乐支撑"],
        source_name="Mutopia Project",
        license_label="Public Domain",
        source_url="https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=3",
        midi_url="https://www.mutopiaproject.org/ftp/BachJS/BWV1043/concerto-in-d-minor/concerto-in-d-minor-mids.zip",
        pdf_url="https://www.mutopiaproject.org/ftp/BachJS/BWV1043/concerto-in-d-minor/concerto-in-d-minor-a4-pdfs.zip",
        part_names=["Solo Violin I", "Solo Violin II", "Violin I", "Violin II", "Viola", "Cello"],
        part_roles=["独奏主角", "独奏主角", "弦乐高声部", "弦乐应答", "中声部", "低音根基"],
        study_kind="独奏主角",
    ),
    OpenScoreWork(
        id="beethoven-violin-concerto-mvt1",
        title="Violin Concerto in D major, Op.61 - I",
        composer="L. V. Beethoven",
        level="独奏主角",
        form="小提琴与管弦乐",
        instrumentation=["Solo Violin", "Flute", "Oboe", "Clarinet", "Bassoon", "Horn", "Trumpet", "Timpani", "Strings"],
        description="真实公版小提琴协奏曲 MIDI。独奏小提琴与完整管弦乐队对话，篇幅和编制都更接近真实音乐会作品；适合先听独奏线，再逐步打开木管、铜管、弦乐和定音鼓。",
        listen_focus=["独奏小提琴进入", "木管回应", "弦乐铺底", "铜管/定音鼓强调", "独奏与乐队问答"],
        source_name="Mutopia Project",
        license_label="Public Domain",
        source_url="https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=990",
        midi_url="https://www.mutopiaproject.org/ftp/BeethovenLv/O61/violin_concerto_1/violin_concerto_1.mid",
        pdf_url="https://www.mutopiaproject.org/ftp/BeethovenLv/O61/violin_concerto_1/violin_concerto_1-a4.pdf",
        part_names=["Flute", "Oboe", "Clarinet", "Bassoon", "Horn I", "Horn II", "Timpani", "Solo Violin", "Violin I", "Violin II", "Viola", "Cello", "Bass"],
        part_roles=["木管高声部", "木管色彩", "木管中声部", "木管低声部", "铜管支撑", "铜管支撑", "节奏重音", "独奏主角", "弦乐主线", "弦乐应答", "中声部", "低音线", "低音根基"],
        study_kind="独奏主角",
    ),
    OpenScoreWork(
        id="vivaldi-four-seasons-spring",
        title="The Four Seasons - Spring",
        composer="Antonio Vivaldi",
        level="完整协奏曲",
        form="小提琴协奏曲",
        instrumentation=["Solo Violin", "Violin I", "Violin II", "Viola", "Cello"],
        description="公版/开放授权完整 MIDI。它比练习型四声部更像一首完整成品曲：独奏小提琴负责最醒目的旋律与炫技，弦乐组负责呼应、铺底和节奏推动。",
        listen_focus=["独奏小提琴主题", "第一小提琴呼应", "第二小提琴织体", "中低声部支撑", "全弦乐合奏推进"],
        source_name="Gaurav Sharma Classical Music Player / Mutopia",
        license_label="Creative Commons Attribution-ShareAlike",
        source_url="https://gauravsharma.com/sound/classical-music-player/",
        midi_url="https://gauravsharma.com/sound/classical-music-player/midi/vivaldi-four-seasons-spring.mid",
        pdf_url="https://www.mutopiaproject.org/ftp/VivaldiA/O8/spring/spring-a4.pdf",
        part_names=["Solo Violin", "Violin I", "Violin II", "Viola", "Cello"],
        part_roles=["独奏主角", "高声部呼应", "内声部织体", "中声部", "低音根基"],
    ),
    OpenScoreWork(
        id="bach-brandenburg5-mvt3",
        title="Brandenburg Concerto No.5 - III. Allegro",
        composer="J. S. Bach",
        level="完整协奏曲",
        form="室内协奏曲",
        instrumentation=["Flute", "Violin", "Viola", "Cello", "Bass", "Harpsichord"],
        description="真实完整 MIDI。长笛、小提琴和羽管键琴互相竞奏，低音组维持和声骨架，适合观察“主角不止一个”的复调编曲。",
        listen_focus=["长笛线条", "小提琴线条", "羽管键琴快速织体", "大提琴/低音支撑", "多主角交替"],
        source_name="Gaurav Sharma Classical Music Player / Mutopia",
        license_label="Public Domain",
        source_url="https://gauravsharma.com/sound/classical-music-player/",
        midi_url="https://gauravsharma.com/sound/classical-music-player/midi/bach-brandenburg5-3.mid",
        pdf_url="https://www.mutopiaproject.org/ftp/BachJS/BWV1050/brand5-3/brand5-3-a4.pdf",
        part_names=["Flute", "Violin I", "Violin II", "Viola", "Cello", "Bass", "Harpsichord RH", "Harpsichord LH"],
        part_roles=["旋律主角", "旋律主角", "内声部", "中声部", "低音线", "低音根基", "键盘高声部", "键盘低声部"],
    ),
    OpenScoreWork(
        id="handel-hallelujah",
        title="Messiah - Hallelujah Chorus",
        composer="G. F. Handel",
        level="完整合唱管弦",
        form="合唱与乐队",
        instrumentation=["Oboe", "Bassoon", "Trumpet", "Timpani", "Strings", "SATB Choir"],
        description="真实完整 MIDI。包含乐队和 SATB 合唱，适合把人声四部、弦乐、铜管和定音鼓分开听，再合起来理解大型合唱作品的层次。",
        listen_focus=["女高音主线", "四部合唱和声", "小号辉煌色彩", "定音鼓重音", "弦乐节奏支撑"],
        source_name="Gaurav Sharma Classical Music Player / Mutopia",
        license_label="Public Domain",
        source_url="https://gauravsharma.com/sound/classical-music-player/",
        midi_url="https://gauravsharma.com/sound/classical-music-player/midi/handel-hallelujah.mid",
        pdf_url="https://www.mutopiaproject.org/ftp/HandelGF/hallelujah/hallelujah-a4.pdf",
        part_names=["Oboes", "Bassoon", "Trumpets", "Timpani", "Violin I", "Violin II", "Viola", "Cello", "Soprano", "Alto", "Tenor", "Bass"],
        part_roles=["木管色彩", "低木管", "铜管强调", "节奏重音", "弦乐高声部", "弦乐应答", "中声部", "低音线", "女高音", "女低音", "男高音", "男低音"],
    ),
    OpenScoreWork(
        id="dvorak-new-world-largo",
        title="Symphony No.9 From the New World - II. Largo",
        composer="Antonin Dvorak",
        level="完整管弦乐",
        form="交响乐乐章",
        instrumentation=["Flute", "Oboe", "Clarinet", "Bassoon", "Horn", "Trumpet", "Trombone", "Timpani", "Strings"],
        description="真实完整管弦乐 MIDI。旋律线很清楚，配器层次也完整，适合练习听木管旋律、弦乐铺底、铜管色彩和低音支撑如何组成一首完整乐章。",
        listen_focus=["木管主旋律", "弦乐铺底", "圆号色彩", "低音根基", "全乐队进入"],
        source_name="Gaurav Sharma Classical Music Player / Mutopia",
        license_label="Creative Commons Attribution-ShareAlike",
        source_url="https://gauravsharma.com/sound/classical-music-player/",
        midi_url="https://gauravsharma.com/sound/classical-music-player/midi/dvorak-new-world-largo.mid",
        pdf_url="https://www.mutopiaproject.org/ftp/DvorakA/O95/Sym9/Sym9-a4.pdf",
        part_names=["Flute", "Oboe", "Clarinet", "Bassoon", "Horn I", "Horn II", "Trumpet", "Trombone", "Violin I", "Violin II", "Viola", "Cello", "Bass", "Timpani"],
        part_roles=["木管高声部", "木管主旋律", "木管中声部", "木管低声部", "铜管色彩", "铜管色彩", "铜管强调", "低铜管", "弦乐主线", "弦乐应答", "中声部", "低音线", "低音根基", "节奏重音"],
    ),
    OpenScoreWork(
        id="mozart-eine-kleine-mvt1",
        title="Eine kleine Nachtmusik, K.525 - I. Allegro",
        composer="W. A. Mozart",
        level="入门完整曲",
        form="弦乐四重奏",
        instrumentation=["Violin I", "Violin II", "Viola", "Cello / Bass"],
        description="真实公版弦乐四重奏 MIDI。四条声部清楚，适合先独听第一小提琴主旋律，再打开内声部和低音看和声支撑。",
        listen_focus=["第一小提琴主题", "第二小提琴应答", "中提琴填充", "低音根音与节奏"],
        source_name="Mutopia Project",
        license_label="Public Domain / CC0 标注",
        source_url="https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=900",
        midi_url="https://www.mutopiaproject.org/ftp/MozartWA/KV525/eine-kleine-nachtmusik-mvt1/eine-kleine-nachtmusik-mvt1.mid",
        pdf_url="https://www.mutopiaproject.org/ftp/MozartWA/KV525/eine-kleine-nachtmusik-mvt1/eine-kleine-nachtmusik-mvt1-a4.pdf",
        part_names=["Violin I", "Violin II", "Viola", "Cello / Bass"],
        part_roles=["主旋律", "应答/内声部", "中声部", "低音根基"],
    ),
    OpenScoreWork(
        id="beethoven-op59-no3-mvt2",
        title="String Quartet Op.59 No.3 - II",
        composer="L. V. Beethoven",
        level="进阶完整曲",
        form="弦乐四重奏",
        instrumentation=["Violin I", "Violin II", "Viola", "Cello"],
        description="真实公版四重奏 MIDI。声部更独立，适合练习听四件乐器如何轮流承担旋律、对位和低音方向。",
        listen_focus=["主题轮换", "对位线条", "中低声部推进", "齐奏与分解织体"],
        source_name="Mutopia Project",
        license_label="Public Domain",
        source_url="https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=1041",
        midi_url="https://www.mutopiaproject.org/ftp/BeethovenLv/O59/Quartet_Opus_59No3_2/Quartet_Opus_59No3_2.mid",
        pdf_url="https://www.mutopiaproject.org/ftp/BeethovenLv/O59/Quartet_Opus_59No3_2/Quartet_Opus_59No3_2-a4.pdf",
        part_names=["Violin I", "Violin II", "Viola", "Cello"],
        part_roles=["旋律/对位", "对位/伴奏", "中声部", "低音线"],
    ),
    OpenScoreWork(
        id="mozart-piano-quartet-k478",
        title="Piano Quartet K.478",
        composer="W. A. Mozart",
        level="室内乐完整曲",
        form="钢琴四重奏",
        instrumentation=["Violin", "Viola", "Cello", "Piano"],
        description="真实公版钢琴四重奏 MIDI。比弦乐四重奏多了钢琴这个复合声部，适合观察钢琴如何同时承担和声、节奏与旋律对话。",
        listen_focus=["钢琴织体", "小提琴主题", "中提琴内声部", "大提琴低音线"],
        source_name="Mutopia Project",
        license_label="Public Domain",
        source_url="https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=499",
        midi_url="https://www.mutopiaproject.org/ftp/MozartWA/KV478/k478/k478-mids.zip",
        pdf_url="https://www.mutopiaproject.org/ftp/MozartWA/KV478/k478/k478-a4.pdf",
        part_names=["Violin", "Viola", "Cello", "Piano RH", "Piano LH"],
        part_roles=["旋律/对话", "中声部", "低音线", "钢琴高声部", "钢琴低声部"],
    ),
    OpenScoreWork(
        id="beethoven-symphony7-mvt2",
        title="Symphony No.7, Op.92 - II. Allegretto",
        composer="L. V. Beethoven",
        level="完整管弦乐",
        form="交响乐片段",
        instrumentation=["Flute", "Oboe", "Clarinet", "Bassoon", "Horn", "Trumpet", "Timpani", "Violin I", "Violin II", "Viola", "Cello", "Bass"],
        description="真实公版管弦乐 MIDI。第二乐章层次清楚，适合把木管、铜管、定音鼓和弦乐分开独听，再合起来理解交响乐织体。",
        listen_focus=["低音固定节奏", "弦乐层层叠加", "木管色彩", "铜管强调"],
        source_name="Mutopia Project",
        license_label="Public Domain",
        source_url="https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=595",
        midi_url="https://www.mutopiaproject.org/ftp/BeethovenLv/O92/Symphony7_2/Symphony7_2.mid",
        pdf_url="https://www.mutopiaproject.org/ftp/BeethovenLv/O92/Symphony7_2/Symphony7_2-a4.pdf",
        part_names=["Flute", "Oboe", "Clarinet", "Bassoon", "Horn I", "Horn II", "Timpani", "Violin I", "Violin II", "Viola", "Cello", "Bass"],
        part_roles=["木管高声部", "木管色彩", "木管中声部", "木管低声部", "铜管支撑", "铜管支撑", "节奏重音", "弦乐主线", "弦乐应答", "中声部", "低音线", "低音根基"],
    ),
    OpenScoreWork(
        id="beethoven-egmont-overture",
        title="Overture to Egmont, Op.84",
        composer="L. V. Beethoven",
        level="完整管弦乐",
        form="序曲",
        instrumentation=["Flute I", "Flute II", "Oboe", "Clarinet", "Bassoon", "Horn", "Trumpet", "Timpani", "Violin I", "Violin II", "Viola", "Cello", "Bass"],
        description="真实公版序曲 MIDI。比室内乐更接近电影配乐的推进感，可以逐个独听木管、铜管、定音鼓和弦乐如何制造戏剧张力。",
        listen_focus=["弦乐推进", "铜管进入", "定音鼓重音", "木管对比"],
        source_name="Mutopia Project",
        license_label="Public Domain",
        source_url="https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=547",
        midi_url="https://www.mutopiaproject.org/ftp/BeethovenLv/O84/Egmont/Egmont.mid",
        pdf_url="https://www.mutopiaproject.org/ftp/BeethovenLv/O84/Egmont/Egmont-a4.pdf",
        part_names=["Flute I", "Flute II", "Oboe", "Clarinet", "Bassoon", "Horn I", "Horn II", "Trumpet I", "Timpani", "Violin I", "Violin II", "Viola", "Cello", "Bass"],
        part_roles=["木管高声部", "木管高声部", "木管色彩", "木管中声部", "木管低声部", "铜管支撑", "铜管支撑", "铜管强调", "节奏重音", "弦乐主线", "弦乐应答", "中声部", "低音线", "低音根基"],
    ),
    OpenScoreWork(
        id="beethoven-symphony5-mvt1",
        title="Symphony No.5, Op.67 - I. Allegro con brio",
        composer="L. V. Beethoven",
        level="完整管弦乐",
        form="交响乐乐章",
        instrumentation=["Flute", "Oboe", "Clarinet", "Bassoon", "Horn", "Trumpet", "Timpani", "Violin I", "Violin II", "Viola", "Cello", "Bass"],
        description="真实公版管弦乐 MIDI。主题动机非常鲜明，适合先独听弦乐动机，再打开木管、铜管和定音鼓观察全乐队如何放大能量。",
        listen_focus=["命运动机", "弦乐齐奏", "木管接续", "铜管与定音鼓强化"],
        source_name="Mutopia Project",
        license_label="Public Domain",
        source_url="https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=941",
        midi_url="https://www.mutopiaproject.org/ftp/BeethovenLv/O67/Symphony5_1/Symphony5_1.mid",
        pdf_url="https://www.mutopiaproject.org/ftp/BeethovenLv/O67/Symphony5_1/Symphony5_1-a4.pdf",
        part_names=["Flute", "Oboe", "Clarinet", "Bassoon", "Horn I", "Horn II", "Timpani", "Violin I", "Violin II", "Viola", "Cello", "Bass"],
        part_roles=["木管高声部", "木管色彩", "木管中声部", "木管低声部", "铜管支撑", "铜管支撑", "节奏重音", "弦乐主线", "弦乐应答", "中声部", "低音线", "低音根基"],
    ),
)


class MidiParseError(RuntimeError):
    pass


def list_open_score_works() -> list[dict[str, Any]]:
    return [_work_summary(work) for work in OPEN_SCORE_WORKS]


def get_open_score_work(work_id: str) -> dict[str, Any]:
    work = _find_work(work_id)
    parsed = _load_parsed_work(work)
    return {
        **_work_summary(work),
        "tempo_bpm": parsed["tempo_bpm"],
        "duration": parsed["duration"],
        "ticks_per_beat": parsed["ticks_per_beat"],
        "parts": parsed["parts"],
        "cached_at": parsed["cached_at"],
        "midi_size": parsed["midi_size"],
    }


def _find_work(work_id: str) -> OpenScoreWork:
    for work in OPEN_SCORE_WORKS:
        if work.id == work_id:
            return work
    raise KeyError(work_id)


def _work_summary(work: OpenScoreWork) -> dict[str, Any]:
    return {
        "id": work.id,
        "title": work.title,
        "composer": work.composer,
        "level": work.level,
        "form": work.form,
        "instrumentation": work.instrumentation,
        "description": work.description,
        "listen_focus": work.listen_focus,
        "source_name": work.source_name,
        "license_label": work.license_label,
        "source_url": work.source_url,
        "midi_url": work.midi_url,
        "pdf_url": work.pdf_url,
        "study_kind": work.study_kind,
        "part_count": len(work.part_names),
    }


def _open_score_cache_root() -> Path:
    root = get_settings().data_dir / "music-tools" / "open-score-cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _load_parsed_work(work: OpenScoreWork) -> dict[str, Any]:
    root = _open_score_cache_root()
    midi_path = root / f"{work.id}.mid"
    parsed_path = root / f"{work.id}.json"
    if midi_path.is_file() and parsed_path.is_file() and parsed_path.stat().st_mtime >= midi_path.stat().st_mtime:
        try:
            payload = json.loads(parsed_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("parser_version") == OPEN_SCORE_PARSER_VERSION and isinstance(payload.get("parts"), list):
            return payload
    if not midi_path.is_file():
        _download_file(work.midi_url, midi_path)
    parsed = parse_midi_score(midi_path.read_bytes(), work)
    parsed["cached_at"] = time.time()
    parsed["midi_size"] = midi_path.stat().st_size
    parsed_path.write_text(json.dumps(parsed, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return parsed


def _download_file(url: str, path: Path) -> None:
    request = Request(url, headers={"User-Agent": "CodeYun open-score-study"})
    with urlopen(request, timeout=45) as response:
        data = response.read()
    if data.startswith(b"PK"):
        data = _select_midi_from_zip(data)
    if not data.startswith(b"MThd"):
        raise MidiParseError("Downloaded file is not a MIDI file")
    tmp_path = path.with_suffix(".mid.tmp")
    tmp_path.write_bytes(data)
    tmp_path.replace(path)


def _select_midi_from_zip(data: bytes) -> bytes:
    with ZipFile(BytesIO(data)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith((".mid", ".midi"))]
        if not names:
            raise MidiParseError("MIDI zip contains no MIDI files")
        names.sort(key=lambda name: (0 if "score" in Path(name).stem.lower() else 1, -archive.getinfo(name).file_size, name))
        return archive.read(names[0])


def parse_midi_score(data: bytes, work: OpenScoreWork) -> dict[str, Any]:
    parser = _MidiReader(data)
    header = parser.read_chunk()
    if header[0] != b"MThd" or len(header[1]) < 6:
        raise MidiParseError("Invalid MIDI header")
    ticks_per_beat = int.from_bytes(header[1][4:6], "big")
    if ticks_per_beat & 0x8000:
        raise MidiParseError("SMPTE MIDI timing is not supported")

    raw_tracks: list[dict[str, Any]] = []
    tempo_events: list[tuple[int, int]] = [(0, 500000)]
    while not parser.done:
        chunk_type, payload = parser.read_chunk()
        if chunk_type != b"MTrk":
            continue
        track = _parse_track(payload)
        raw_tracks.append(track)
        tempo_events.extend(track["tempo_events"])

    tempo_map = _build_tempo_map(tempo_events, ticks_per_beat)
    note_tracks = [track for track in raw_tracks if track["notes"]]
    parts: list[dict[str, Any]] = []
    for index, track in enumerate(note_tracks):
        fallback_name = work.part_names[index] if index < len(work.part_names) else f"Part {index + 1}"
        role = work.part_roles[index] if index < len(work.part_roles) else "声部"
        name = _clean_track_name(track["name"]) or fallback_name
        if len(note_tracks) == len(work.part_names):
            name = fallback_name
        notes = [
            {
                "pitch": note["pitch"],
                "start": round(_tick_to_seconds(note["start_tick"], tempo_map), 4),
                "duration": round(max(0.04, _tick_to_seconds(note["end_tick"], tempo_map) - _tick_to_seconds(note["start_tick"], tempo_map)), 4),
                "velocity": note["velocity"],
            }
            for note in track["notes"]
            if note["end_tick"] > note["start_tick"]
        ]
        if not notes:
            continue
        parts.append(
            {
                "name": name,
                "role": role,
                "program": track["program"],
                "channel": track["channel"],
                "note_count": len(notes),
                "range": [_midi_to_name(min(note["pitch"] for note in notes)), _midi_to_name(max(note["pitch"] for note in notes))],
                "notes": notes,
                "_order": index,
            }
        )

    parts.sort(key=lambda part: (0 if part["role"] == "独奏主角" else 1, part["_order"]))
    for part in parts:
        part.pop("_order", None)
    duration = max((note["start"] + note["duration"] for part in parts for note in part["notes"]), default=0)
    return {
        "parser_version": OPEN_SCORE_PARSER_VERSION,
        "tempo_bpm": round(60000000 / tempo_events[0][1], 2),
        "duration": round(duration, 3),
        "ticks_per_beat": ticks_per_beat,
        "parts": parts,
    }


class _MidiReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.index = 0

    @property
    def done(self) -> bool:
        return self.index >= len(self.data)

    def read_chunk(self) -> tuple[bytes, bytes]:
        if self.index + 8 > len(self.data):
            raise MidiParseError("Unexpected end of MIDI file")
        chunk_type = self.data[self.index : self.index + 4]
        length = int.from_bytes(self.data[self.index + 4 : self.index + 8], "big")
        start = self.index + 8
        end = start + length
        if end > len(self.data):
            raise MidiParseError("Invalid MIDI chunk length")
        self.index = end
        return chunk_type, self.data[start:end]


def _parse_track(data: bytes) -> dict[str, Any]:
    index = 0
    tick = 0
    status: int | None = None
    name = ""
    program: int | None = None
    channel: int | None = None
    tempo_events: list[tuple[int, int]] = []
    active: dict[tuple[int, int], list[tuple[int, int]]] = {}
    notes: list[dict[str, Any]] = []

    while index < len(data):
        delta, index = _read_varlen(data, index)
        tick += delta
        event = data[index]
        if event < 0x80:
            if status is None:
                raise MidiParseError("Running status without prior status")
            event_type = status
            first_data: int | None = event
            index += 1
        else:
            event_type = event
            index += 1
            first_data = None
            if event_type < 0xF0:
                status = event_type

        if event_type == 0xFF:
            meta_type = data[index]
            index += 1
            length, index = _read_varlen(data, index)
            payload = data[index : index + length]
            index += length
            if meta_type == 0x03:
                name = payload.decode("utf-8", errors="replace").strip()
            elif meta_type == 0x51 and len(payload) == 3:
                tempo_events.append((tick, int.from_bytes(payload, "big")))
            elif meta_type == 0x2F:
                break
            continue

        if event_type in (0xF0, 0xF7):
            length, index = _read_varlen(data, index)
            index += length
            continue

        command = event_type & 0xF0
        event_channel = event_type & 0x0F
        size = 1 if command in (0xC0, 0xD0) else 2
        values: list[int] = []
        if first_data is not None:
            values.append(first_data)
        while len(values) < size:
            values.append(data[index])
            index += 1

        if channel is None and command in (0x80, 0x90, 0xA0, 0xB0, 0xC0, 0xE0):
            channel = event_channel
        if command == 0xC0:
            program = values[0]
        elif command == 0x90 and len(values) == 2:
            pitch, velocity = values
            key = (event_channel, pitch)
            if velocity > 0:
                active.setdefault(key, []).append((tick, velocity))
            else:
                _close_note(active, notes, key, tick)
        elif command == 0x80 and len(values) == 2:
            pitch = values[0]
            _close_note(active, notes, (event_channel, pitch), tick)

    for key, starts in active.items():
        for start_tick, velocity in starts:
            if tick > start_tick:
                notes.append({"pitch": key[1], "start_tick": start_tick, "end_tick": tick, "velocity": velocity})

    notes.sort(key=lambda item: (item["start_tick"], item["pitch"], item["end_tick"]))
    return {"name": name, "program": program, "channel": channel, "tempo_events": tempo_events, "notes": notes}


def _close_note(active: dict[tuple[int, int], list[tuple[int, int]]], notes: list[dict[str, Any]], key: tuple[int, int], end_tick: int) -> None:
    starts = active.get(key)
    if not starts:
        return
    start_tick, velocity = starts.pop(0)
    if not starts:
        active.pop(key, None)
    if end_tick > start_tick:
        notes.append({"pitch": key[1], "start_tick": start_tick, "end_tick": end_tick, "velocity": velocity})


def _read_varlen(data: bytes, index: int) -> tuple[int, int]:
    value = 0
    while True:
        if index >= len(data):
            raise MidiParseError("Unexpected end of variable length value")
        byte = data[index]
        index += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, index


def _build_tempo_map(tempo_events: list[tuple[int, int]], ticks_per_beat: int) -> list[dict[str, float]]:
    events = sorted(set(tempo_events), key=lambda item: item[0])
    if not events or events[0][0] != 0:
        events.insert(0, (0, 500000))
    segments: list[dict[str, float]] = []
    elapsed = 0.0
    last_tick = events[0][0]
    last_tempo = events[0][1]
    segments.append({"tick": float(last_tick), "seconds": elapsed, "tempo": float(last_tempo), "ticks_per_beat": float(ticks_per_beat)})
    for tick, tempo in events[1:]:
        if tick < last_tick:
            continue
        elapsed += ((tick - last_tick) * last_tempo) / ticks_per_beat / 1_000_000
        last_tick = tick
        last_tempo = tempo
        segments.append({"tick": float(tick), "seconds": elapsed, "tempo": float(tempo), "ticks_per_beat": float(ticks_per_beat)})
    return segments


def _tick_to_seconds(tick: int, tempo_map: list[dict[str, float]]) -> float:
    segment = tempo_map[0]
    for item in tempo_map:
        if item["tick"] > tick:
            break
        segment = item
    return segment["seconds"] + ((tick - segment["tick"]) * segment["tempo"]) / segment["ticks_per_beat"] / 1_000_000


def _clean_track_name(name: str) -> str:
    cleaned = " ".join(name.replace(":", " ").split())
    return cleaned[:40]


def _midi_to_name(pitch: int) -> str:
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[pitch % 12]}{pitch // 12 - 1}"
