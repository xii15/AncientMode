"""ALGOCOMPO GUI v2 corrected.

A self-contained Tkinter application for generating monophonic Matrix melodies
as Standard MIDI Files. All musical parameters are entered in the GUI.
No external Python packages are required.
"""
from __future__ import annotations

import ast
import json
import math
import random
import re
import struct
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

APP_TITLE = "ANCIENT MODE Matrix Melody Generator v7"
TICKS_PER_QUARTER = 20160

MOTION_NAMES = ["R", "L", "U", "D"] + [x for n in range(2, 11) for x in (f"U{n}", f"D{n}")]
RHYTHM_NAMES = ["64", "64d", "32", "32d", "16", "16d", "8", "8d", "4", "4d", "2", "2d", "1", "1d"]
BASE_VALUES = ["64", "32", "16", "8", "4", "2", "1"]
TUPLET_BASE_VALUES = ["64", "32", "16", "8", "4"]
TUPLET_MODES = ["Off", "Mixed", "Tuplets only"]

# Stores the last MIDI output folder between application sessions.
LAST_OUTPUT_FOLDER_FILE = Path.home() / ".algocompo_last_output_folder.json"

def load_last_output_folder() -> Path:
    """Return the last valid MIDI output folder, or the user's home folder."""
    try:
        data = json.loads(LAST_OUTPUT_FOLDER_FILE.read_text(encoding="utf-8"))
        folder = Path(data.get("folder", "")).expanduser()
        if folder.is_dir():
            return folder
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return Path.home()

def save_last_output_folder(folder: Path) -> None:
    """Persist the last MIDI output folder for the next file dialogue."""
    try:
        LAST_OUTPUT_FOLDER_FILE.write_text(
            json.dumps({"folder": str(folder.expanduser().resolve())}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        # Folder memory is a convenience. MIDI creation should still continue
        # if macOS does not allow this small preferences file to be written.
        pass

DEFAULT_CONFIG = {
    "title": "matrix_melody",
    "time_signature_numerator": 4,
    "time_signature_denominator": 4,
    "measures": 5,
    "tempo_bpm": 60.0,
    "midi_program": 73,
    "midi_channel": 1,
    "low_note": 55,
    "high_note": 76,
    "velocity_low": 55,
    "velocity_high": 77,
    "start_note": 0,
    "pitch_repetition_max": 1,
    "pitch_repetition_chance": 0.0,
    "backward_motion_chance": 5.0,
    "motion_weights": {name: (25.0 if name in {"R", "L", "U", "D"} else 0.0) for name in MOTION_NAMES},
    "motion_max_runs": {name: 0 for name in MOTION_NAMES},
    "atomic_motion_chance": 0.0,
    "atomic_motion_patterns": "",
    "rest_min": "8",
    "rest_max": "8",
    "rest_chance": 0.0,
    "rhythm_weights": {name: (50.0 if name in {"16", "8"} else 0.0) for name in RHYTHM_NAMES},
    "rhythm_group_min": {name: 1 for name in RHYTHM_NAMES},
    "rhythm_group_max": {name: 1 for name in RHYTHM_NAMES},
    "tuplet_mode": "Off",
    "tuplet_full_beat_only": True,
    "tuplet_base_min": "32",
    "tuplet_base_max": "16",
    "tuplet_ratios": "3,5",
    "tuplet_chance": 40,
    "atomic_rhythm_chance": 0.0,
    "atomic_rhythm_patterns": "",
    "grace_enabled": False,
    "grace_value": "64d",
    "grace_motions": "R,L,U,D",
    "grace_count_min": 1,
    "grace_count_max": 2,
    "grace_chance": 33,
    "grace_forbidden_main_values": "16,16d,16t,16t5,16t7,16t9,32,32d,32t,32t5,32t7,32t9,64",
    "grace_forbidden_previous_values": "32,32d,32t,32t5,32t7,32t9,64",
    "rhythm_motion_rules": "",
    "seed_spec": "1-7",
}


def motion_value(token: str) -> int:
    token = token.strip().upper()
    if token == "R":
        return 1
    if token == "L":
        return -1
    match = re.fullmatch(r"([UD])(10|[1-9])?", token)
    if not match:
        raise ValueError(f"Invalid motion: {token!r}")
    direction, multiplier = match.groups()
    return (5 if direction == "U" else -5) * int(multiplier or 1)


def opposite_motion(first: str, second: str) -> bool:
    return motion_value(first) + motion_value(second) == 0


def duration_beats(token: str) -> float:
    token = token.strip().lower()
    dotted = token.endswith("d")
    if dotted:
        token = token[:-1]
    if token not in BASE_VALUES:
        raise ValueError(f"Invalid rhythmic value: {token!r}")
    beats = 4.0 / int(token)
    return beats * (1.5 if dotted else 1.0)


def tuplet_normal_count(ratio: int) -> int:
    if ratio < 2:
        raise ValueError("Tuplet ratio must be at least 2.")
    return 2 ** int(math.floor(math.log2(ratio)))


def tuplet_note_duration(base: str, ratio: int) -> float:
    return duration_beats(base) * tuplet_normal_count(ratio) / ratio


def parse_motion_pattern(line: str) -> list[str]:
    tokens = [item.strip().upper() for item in line.split(",") if item.strip()]
    if not tokens:
        raise ValueError("An atomic motion pattern cannot be empty.")
    for token in tokens:
        motion_value(token)
    return tokens


def parse_rhythm_token(token: str) -> tuple[str, float, int | None]:
    """Parse 8d, 16t, 16t5, 8dt7, etc. Bare t means 3:2."""
    token = token.strip().lower()
    match = re.fullmatch(r"(64|32|16|8|4|2|1)(d?)(?:t(\d+)?)?", token)
    if not match:
        raise ValueError(f"Invalid rhythm token: {token!r}")
    base, dotted, ratio_text = match.groups()
    if "t" not in token:
        normal = base + dotted
        return token, duration_beats(normal), None
    ratio = int(ratio_text or 3)
    return token, tuplet_note_duration(base + dotted, ratio), ratio


def parse_rhythm_pattern(line: str) -> list[tuple[str, float, int | None]]:
    tokens = [parse_rhythm_token(item) for item in line.split(",") if item.strip()]
    if not tokens:
        raise ValueError("An atomic rhythm pattern cannot be empty.")
    return tokens


def parse_rhythm_motion_rules(text: str) -> dict[str, list[str]]:
    """Parse exact previous-rhythm-to-motion restrictions.

    One rule per line, using ``RHYTHM:MOTION,MOTION``. Examples::

        32:U,D
        16:R,L,U,D
        16t5:U2,D2

    A rhythm value not listed here remains free and uses the ordinary enabled
    motion table. Rules apply to the outgoing main-note motion after a note of
    the specified rhythmic value.
    """
    rules: dict[str, list[str]] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(
                f"Rhythm-motion rule line {line_number} must use VALUE:MOTION,MOTION."
            )
        rhythm_text, motion_text = line.split(":", 1)
        rhythm_token, _duration, _ratio = parse_rhythm_token(rhythm_text.strip())
        motions = parse_motion_pattern(motion_text)
        rules[rhythm_token.lower()] = motions
    return rules


def parse_seed_spec(text: str) -> list[int | None]:
    text = text.strip()
    if not text:
        return [None]
    if re.fullmatch(r"-?\d+", text):
        return [int(text)]
    match = re.fullmatch(r"(-?\d+)\s*-\s*(-?\d+)", text)
    if not match:
        raise ValueError("Seed must be blank, one integer, or an inclusive range such as 2-8.")
    start, end = map(int, match.groups())
    step = 1 if end >= start else -1
    return list(range(start, end + step, step))


def weighted_choice(items: list[Any], weights: list[float], rng: random.Random) -> Any:
    if not items or sum(weights) <= 0:
        raise RuntimeError("No positive-weight choice remains after applying the constraints.")
    return rng.choices(items, weights=weights, k=1)[0]


def variable_length(value: int) -> bytes:
    if value < 0:
        raise ValueError("MIDI delta time cannot be negative.")
    buffer = value & 0x7F
    output = bytearray([buffer])
    while value > 0x7F:
        value >>= 7
        output.insert(0, (value & 0x7F) | 0x80)
    return bytes(output)


def meta_event(delta: int, event_type: int, data: bytes) -> bytes:
    return variable_length(delta) + bytes([0xFF, event_type]) + variable_length(len(data)) + data


def midi_message(delta: int, status: int, *data: int) -> bytes:
    return variable_length(delta) + bytes([status, *data])


def write_midi(events: list[dict[str, Any]], output: Path, config: dict[str, Any]) -> None:
    channel = config["midi_channel"] - 1
    numerator = config["time_signature_numerator"]
    denominator = config["time_signature_denominator"]
    track = bytearray()
    clip_name = config.get("midi_clip_name", config["title"])
    track += meta_event(0, 0x03, clip_name.encode("utf-8"))
    mpq = round(60_000_000 / config["tempo_bpm"])
    track += meta_event(0, 0x51, mpq.to_bytes(3, "big"))
    track += meta_event(0, 0x58, bytes([numerator, int(math.log2(denominator)), 24, 8]))
    track += midi_message(0, 0xC0 | channel, config["midi_program"])
    pending = 0
    for event in events:
        ticks = max(1, round(event["duration"] * TICKS_PER_QUARTER))
        if event["kind"] == "rest":
            pending += ticks
        else:
            track += midi_message(pending, 0x90 | channel, event["pitch"], event["velocity"])
            track += midi_message(ticks, 0x80 | channel, event["pitch"], 0)
            pending = 0
    track += meta_event(pending, 0x2F, b"")
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, TICKS_PER_QUARTER)
    chunk = b"MTrk" + struct.pack(">I", len(track)) + bytes(track)
    output.write_bytes(header + chunk)


@dataclass
class State:
    pitch: int
    previous_motion: str | None = None
    motion_run: int = 0
    repeated_pitch_count: int = 1
    rhythm_token: str | None = None
    rhythm_run: int = 0


class MelodyGenerator:
    def __init__(self, config: dict[str, Any], seed: int | None):
        self.c = config
        self.rng = random.Random(seed)
        start = config["start_note"] or self.rng.randint(config["low_note"], config["high_note"])
        self.state = State(start)
        self.atomic_motion_queue: list[str] = []
        self.atomic_rhythm_queue: list[tuple[str, float, int | None]] = []
        self.tuplet_queue: list[tuple[str, float, int | None]] = []
        self.motion_patterns = [parse_motion_pattern(x) for x in config["atomic_motion_patterns"].splitlines() if x.strip()]
        self.rhythm_patterns = [parse_rhythm_pattern(x) for x in config["atomic_rhythm_patterns"].splitlines() if x.strip()]
        self.grace_motions = parse_motion_pattern(config["grace_motions"]) if config["grace_enabled"] else []
        self.previous_main_rhythm_token: str | None = None
        self.rhythm_motion_rules = parse_rhythm_motion_rules(
            config["rhythm_motion_rules"]
        )

    def total_beats(self) -> float:
        return self.c["measures"] * self.c["time_signature_numerator"] * (4 / self.c["time_signature_denominator"])

    def choose_motion(self, override: list[str] | None = None) -> str:
        if override is None and self.atomic_motion_queue:
            token = self.atomic_motion_queue.pop(0)
            destination = self.state.pitch + motion_value(token)
            if self.c["low_note"] <= destination <= self.c["high_note"]:
                return token
        if override is None and self.motion_patterns and self.rng.random() < self.c["atomic_motion_chance"] / 100:
            self.atomic_motion_queue = list(self.rng.choice(self.motion_patterns))
            return self.choose_motion()

        items, weights = [], []
        for token in override or MOTION_NAMES:
            weight = 1.0 if override is not None else self.c["motion_weights"].get(token, 0.0)
            if weight <= 0:
                continue
            destination = self.state.pitch + motion_value(token)
            if not self.c["low_note"] <= destination <= self.c["high_note"]:
                continue
            maximum = self.c["motion_max_runs"].get(token, 0)
            if maximum > 0 and self.state.previous_motion == token and self.state.motion_run >= maximum:
                continue
            if self.state.previous_motion and opposite_motion(self.state.previous_motion, token):
                weight *= self.c["backward_motion_chance"] / 100
            if weight > 0:
                items.append(token)
                weights.append(weight)
        return weighted_choice(items, weights, self.rng)

    def apply_motion(self, token: str) -> int:
        self.state.pitch += motion_value(token)
        self.state.motion_run = self.state.motion_run + 1 if token == self.state.previous_motion else 1
        self.state.previous_motion = token
        self.state.repeated_pitch_count = 1
        return self.state.pitch

    def allowed_motions_from_previous_rhythm(self) -> list[str] | None:
        """Return a local motion restriction, or None for free selection."""
        if self.previous_main_rhythm_token is None:
            return None
        return self.rhythm_motion_rules.get(
            self.previous_main_rhythm_token.strip().lower()
        )

    def next_main_pitch(self) -> tuple[int, str]:
        if self.c["pitch_repetition_max"] > 1 and self.state.repeated_pitch_count < self.c["pitch_repetition_max"]:
            if self.rng.random() < self.c["pitch_repetition_chance"] / 100:
                self.state.repeated_pitch_count += 1
                return self.state.pitch, "S"

        restricted_motions = self.allowed_motions_from_previous_rhythm()
        token = self.choose_motion(restricted_motions)
        return self.apply_motion(token), token

    def start_tuplet_group(self) -> None:
        ratios = [int(x.strip()) for x in self.c["tuplet_ratios"].split(",") if x.strip()]
        ratio = self.rng.choice(ratios)
        a = TUPLET_BASE_VALUES.index(self.c["tuplet_base_min"])
        b = TUPLET_BASE_VALUES.index(self.c["tuplet_base_max"])
        lo, hi = sorted((a, b))
        base = self.rng.choice(TUPLET_BASE_VALUES[lo:hi + 1])
        duration = tuplet_note_duration(base, ratio)
        self.tuplet_queue = [(f"{base}t{ratio}", duration, ratio)] * ratio

    def choose_normal_rhythm(self) -> tuple[str, float, None]:
        items = [x for x in RHYTHM_NAMES if self.c["rhythm_weights"].get(x, 0) > 0]
        weights = [self.c["rhythm_weights"][x] for x in items]
        current = self.state.rhythm_token
        if current:
            minimum = self.c["rhythm_group_min"][current]
            maximum = self.c["rhythm_group_max"][current]
            if self.state.rhythm_run < minimum:
                token = current
            elif self.state.rhythm_run < maximum and self.rng.random() < 0.5:
                token = current
            else:
                token = weighted_choice(items, weights, self.rng)
                self.state.rhythm_token = token
                self.state.rhythm_run = 0
        else:
            token = weighted_choice(items, weights, self.rng)
            self.state.rhythm_token = token
            self.state.rhythm_run = 0
        self.state.rhythm_run += 1
        return token, duration_beats(token), None

    def choose_rhythm(self) -> tuple[str, float, int | None]:
        if self.atomic_rhythm_queue:
            return self.atomic_rhythm_queue.pop(0)
        if self.rhythm_patterns and self.rng.random() < self.c["atomic_rhythm_chance"] / 100:
            self.atomic_rhythm_queue = list(self.rng.choice(self.rhythm_patterns))
            return self.atomic_rhythm_queue.pop(0)
        if self.tuplet_queue:
            return self.tuplet_queue.pop(0)
        use_tuplet = self.c["tuplet_mode"] == "Tuplets only" or (
            self.c["tuplet_mode"] == "Mixed" and self.rng.random() < self.c["tuplet_chance"] / 100
        )
        if use_tuplet:
            self.start_tuplet_group()
            return self.tuplet_queue.pop(0)
        return self.choose_normal_rhythm()

    def grace_events(
        self,
        main_duration: float,
        main_rhythm_token: str,
    ) -> tuple[list[dict[str, Any]], float]:
        """Create grace notes only when current and previous values permit them."""
        if not self.c["grace_enabled"]:
            return [], main_duration

        forbidden_current = {
            item.strip().lower()
            for item in self.c["grace_forbidden_main_values"].split(",")
            if item.strip()
        }
        if main_rhythm_token.strip().lower() in forbidden_current:
            return [], main_duration

        forbidden_previous = {
            item.strip().lower()
            for item in self.c["grace_forbidden_previous_values"].split(",")
            if item.strip()
        }
        if (
            self.previous_main_rhythm_token is not None
            and self.previous_main_rhythm_token.strip().lower() in forbidden_previous
        ):
            return [], main_duration

        if self.rng.random() >= self.c["grace_chance"] / 100:
            return [], main_duration

        token, grace_duration, _ = parse_rhythm_token(self.c["grace_value"])
        requested = self.rng.randint(
            self.c["grace_count_min"],
            self.c["grace_count_max"],
        )
        max_count = max(0, math.ceil(main_duration / grace_duration) - 1)
        count = min(requested, max_count)
        if count < self.c["grace_count_min"]:
            return [], main_duration

        original_state = State(**vars(self.state))
        result = []
        try:
            for _ in range(count):
                motion = self.choose_motion(self.grace_motions)
                pitch = self.apply_motion(motion)
                result.append({
                    "kind": "note",
                    "duration": grace_duration,
                    "pitch": pitch,
                    "velocity": self.rng.randint(
                        self.c["velocity_low"],
                        self.c["velocity_high"],
                    ),
                    "role": "grace",
                    "rhythm": token,
                })
        except RuntimeError:
            self.state = original_state
            return [], main_duration

        used = sum(item["duration"] for item in result)
        if used >= main_duration:
            self.state = original_state
            return [], main_duration
        return result, main_duration - used

    def choose_rest(self) -> float:
        a = BASE_VALUES.index(self.c["rest_min"])
        b = BASE_VALUES.index(self.c["rest_max"])
        lo, hi = sorted((a, b))
        return duration_beats(self.rng.choice(BASE_VALUES[lo:hi + 1]))

    def generate(self) -> list[dict[str, Any]]:
        target = self.total_beats()
        elapsed = 0.0
        events: list[dict[str, Any]] = []
        first = True
        while elapsed < target - 1e-9:
            if self.rng.random() < self.c["rest_chance"] / 100:
                duration = min(self.choose_rest(), target - elapsed)
                events.append({"kind": "rest", "duration": duration})
                elapsed += duration
                continue
            token, duration, ratio = self.choose_rhythm()
            duration = min(duration, target - elapsed)
            grace, main_duration = self.grace_events(duration, token)
            events.extend(grace)
            elapsed += sum(x["duration"] for x in grace)
            if first:
                if grace:
                    pitch, motion = self.next_main_pitch()
                else:
                    pitch, motion = self.state.pitch, "START"
                first = False
            else:
                pitch, motion = self.next_main_pitch()
            main_duration = min(main_duration, target - elapsed)
            if main_duration <= 0:
                break
            events.append({
                "kind": "note", "duration": main_duration, "pitch": pitch,
                "velocity": self.rng.randint(self.c["velocity_low"], self.c["velocity_high"]),
                "role": "main", "motion": motion, "rhythm": token, "tuplet_ratio": ratio,
            })
            elapsed += main_duration
            self.previous_main_rhythm_token = token
        return events


class ScrollFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        canvas = tk.Canvas(self, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.body = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("1450x920")
        root.minsize(1100, 720)
        self.vars: dict[str, tk.Variable] = {}
        self.motion_enabled: dict[str, tk.BooleanVar] = {}
        self.motion_weights: dict[str, tk.StringVar] = {}
        self.motion_runs: dict[str, tk.StringVar] = {}
        self.rhythm_enabled: dict[str, tk.BooleanVar] = {}
        self.rhythm_weights: dict[str, tk.StringVar] = {}
        self.rhythm_mins: dict[str, tk.StringVar] = {}
        self.rhythm_maxs: dict[str, tk.StringVar] = {}
        self.status = tk.StringVar(value="Ready.")
        self.last_output_folder = load_last_output_folder()
        self.build()
        self.set_config(DEFAULT_CONFIG)

    def new_var(self, key: str, cls: type[tk.Variable] = tk.StringVar) -> tk.Variable:
        var = cls()
        self.vars[key] = var
        return var

    def entry(self, frame, row: int, label: str, key: str, width: int = 10, note: str = "") -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(frame, textvariable=self.new_var(key), width=width).grid(row=row, column=1, sticky="w", padx=6, pady=4)
        if note:
            ttk.Label(frame, text=note, foreground="#555555").grid(row=row, column=2, sticky="w", padx=6)

    def build(self) -> None:
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill="x")
        for text, command in [
            ("Generate MIDI", self.generate), ("Validate", self.validate_only),
            ("Save preset", self.save_preset), ("Load preset", self.load_preset),
            ("Reset defaults", lambda: self.set_config(DEFAULT_CONFIG)),
        ]:
            ttk.Button(toolbar, text=text, command=command).pack(side="left", padx=3)
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        frames = [ScrollFrame(notebook) for _ in range(4)]
        titles = ["General", "Pitch & motions", "Rhythm & rests", "Atomic patterns, grace & seeds"]
        for frame, title in zip(frames, titles):
            notebook.add(frame, text=title)
        self.build_general(frames[0].body)
        self.build_pitch(frames[1].body)
        self.build_rhythm(frames[2].body)
        self.build_atomic(frames[3].body)
        ttk.Label(self.root, textvariable=self.status, anchor="w", padding=8).pack(fill="x")

    def build_general(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="Composition", padding=10)
        box.pack(fill="x", padx=10, pady=10)
        self.entry(box, 0, "Title / filename", "title", width=28)
        self.entry(box, 1, "Measures", "measures")
        self.entry(box, 2, "Tempo BPM", "tempo_bpm")
        ttk.Label(box, text="Time signature").grid(row=3, column=0, sticky="w", padx=6, pady=4)
        ts = ttk.Frame(box)
        ts.grid(row=3, column=1, sticky="w")
        ttk.Entry(ts, textvariable=self.new_var("time_signature_numerator"), width=5).pack(side="left")
        ttk.Label(ts, text=" / ").pack(side="left")
        ttk.Combobox(ts, textvariable=self.new_var("time_signature_denominator"), values=(16, 8, 4, 2), state="readonly", width=5).pack(side="left")
        self.entry(box, 4, "MIDI programme", "midi_program", note="0-127")
        self.entry(box, 5, "MIDI channel", "midi_channel", note="1-16")

    def build_pitch(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="Pitch and expression", padding=10)
        box.pack(fill="x", padx=10, pady=10)
        pitch_fields = [
            ("Lowest MIDI note", "low_note", ""),
            ("Highest MIDI note", "high_note", ""),
            ("Start note", "start_note", "0 = random"),
            ("Velocity from", "velocity_low", ""),
            ("Velocity to", "velocity_high", ""),
            ("Max same-pitch repetitions", "pitch_repetition_max", "1 = none"),
            ("Pitch repetition chance %", "pitch_repetition_chance", "decimals allowed"),
            ("Immediate backward chance %", "backward_motion_chance", "0 = none; 100 = unreduced"),
        ]
        for row, (label, key, note) in enumerate(pitch_fields):
            self.entry(box, row, label, key, width=10, note=note)

        table = ttk.LabelFrame(parent, text="Motions", padding=10)
        table.pack(fill="x", padx=10, pady=10)
        for column, label in enumerate(("Use", "Motion", "Chance-weight", "Max consecutive")):
            ttk.Label(table, text=label, font=("TkDefaultFont", 10, "bold")).grid(row=0, column=column, padx=6, pady=4)
        for row, name in enumerate(MOTION_NAMES, start=1):
            self.motion_enabled[name] = tk.BooleanVar()
            self.motion_weights[name] = tk.StringVar()
            self.motion_runs[name] = tk.StringVar()
            ttk.Checkbutton(table, variable=self.motion_enabled[name]).grid(row=row, column=0)
            ttk.Label(table, text=name, width=8).grid(row=row, column=1)
            ttk.Entry(table, textvariable=self.motion_weights[name], width=12).grid(row=row, column=2, padx=5)
            ttk.Entry(table, textvariable=self.motion_runs[name], width=12).grid(row=row, column=3, padx=5)
        ttk.Label(table, text="Weights are relative. Max consecutive 0 means unlimited.", foreground="#555555").grid(row=len(MOTION_NAMES) + 1, column=0, columnspan=4, sticky="w", pady=8)

        rhythm_motion = ttk.LabelFrame(
            parent,
            text="Motion restrictions from previous rhythm values",
            padding=10,
        )
        rhythm_motion.pack(fill="both", expand=True, padx=10, pady=10)
        ttk.Label(
            rhythm_motion,
            text=(
                "Optional. One rule per line: VALUE:MOTION,MOTION. "
                "Example 32:U,D means that after a 32nd-note main value, "
                "the next main pitch may move only by U or D. "
                "Unlisted values remain free."
            ),
            wraplength=1050,
            justify="left",
        ).pack(anchor="w")
        self.rhythm_motion_rules_text = tk.Text(
            rhythm_motion,
            height=7,
            wrap="none",
        )
        self.rhythm_motion_rules_text.pack(fill="both", expand=True, pady=6)

    def build_rhythm(self, parent) -> None:
        rests = ttk.LabelFrame(parent, text="Rests", padding=10)
        rests.pack(fill="x", padx=10, pady=10)
        ttk.Label(rests, text="Value range").grid(row=0, column=0, sticky="w", padx=6)
        ttk.Combobox(rests, textvariable=self.new_var("rest_min"), values=BASE_VALUES, state="readonly", width=7).grid(row=0, column=1)
        ttk.Label(rests, text="to").grid(row=0, column=2, padx=6)
        ttk.Combobox(rests, textvariable=self.new_var("rest_max"), values=BASE_VALUES, state="readonly", width=7).grid(row=0, column=3)
        self.entry(rests, 1, "Rest chance %", "rest_chance", note="0 = no rests")

        table = ttk.LabelFrame(parent, text="Rhythmic values, weights and per-value grouping", padding=10)
        table.pack(fill="x", padx=10, pady=10)
        for column, label in enumerate(("Use", "Value", "Chance-weight", "Group-minimum", "Group-maximum")):
            ttk.Label(table, text=label, font=("TkDefaultFont", 10, "bold")).grid(row=0, column=column, padx=6)
        for row, name in enumerate(RHYTHM_NAMES, start=1):
            self.rhythm_enabled[name] = tk.BooleanVar()
            self.rhythm_weights[name] = tk.StringVar()
            self.rhythm_mins[name] = tk.StringVar()
            self.rhythm_maxs[name] = tk.StringVar()
            ttk.Checkbutton(table, variable=self.rhythm_enabled[name]).grid(row=row, column=0)
            ttk.Label(table, text=name).grid(row=row, column=1)
            ttk.Entry(table, textvariable=self.rhythm_weights[name], width=12).grid(row=row, column=2, padx=4)
            ttk.Entry(table, textvariable=self.rhythm_mins[name], width=12).grid(row=row, column=3, padx=4)
            ttk.Entry(table, textvariable=self.rhythm_maxs[name], width=12).grid(row=row, column=4, padx=4)
        ttk.Label(table, text="Example: 16 | weight 20 | min 4 | max 4 gives groups of exactly four sixteenth notes.", foreground="#555555").grid(row=len(RHYTHM_NAMES) + 1, column=0, columnspan=5, sticky="w", pady=8)

        tup = ttk.LabelFrame(parent, text="Tuplets", padding=10)
        tup.pack(fill="x", padx=10, pady=10)
        ttk.Label(tup, text="Mode").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Combobox(tup, textvariable=self.new_var("tuplet_mode"), values=TUPLET_MODES, state="readonly", width=16).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(tup, text="Start complete random tuplet groups on a full group boundary", variable=self.new_var("tuplet_full_beat_only", tk.BooleanVar)).grid(row=1, column=0, columnspan=4, sticky="w", padx=6, pady=4)
        ttk.Label(tup, text="Tuplet base value from").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        ttk.Combobox(tup, textvariable=self.new_var("tuplet_base_min"), values=TUPLET_BASE_VALUES, state="readonly", width=7).grid(row=2, column=1, sticky="w")
        ttk.Label(tup, text="to").grid(row=2, column=2, padx=6)
        ttk.Combobox(tup, textvariable=self.new_var("tuplet_base_max"), values=TUPLET_BASE_VALUES, state="readonly", width=7).grid(row=2, column=3, sticky="w")
        self.entry(tup, 3, "Allowed ratios", "tuplet_ratios", width=18, note="3,5,7,9 means 3:2, 5:4, 7:4, 9:8")
        self.entry(tup, 4, "Tuplet chance %", "tuplet_chance", note="Used in Mixed mode")
        ttk.Label(tup, text="For mixed values in one tuplet, use Atomic rhythm patterns.", foreground="#555555").grid(row=5, column=0, columnspan=4, sticky="w", padx=6, pady=8)

    def build_atomic(self, parent) -> None:
        motion = ttk.LabelFrame(parent, text="Atomic motion patterns", padding=10)
        motion.pack(fill="both", expand=True, padx=10, pady=10)
        ttk.Label(motion, text="One complete pattern per line, for example U,U,U,R,D,D").pack(anchor="w")
        self.atomic_motion_text = tk.Text(motion, height=6, wrap="none")
        self.atomic_motion_text.pack(fill="both", expand=True, pady=5)
        row = ttk.Frame(motion)
        row.pack(fill="x")
        ttk.Label(row, text="Atomic motion chance %").pack(side="left")
        ttk.Entry(row, textvariable=self.new_var("atomic_motion_chance"), width=10).pack(side="left", padx=6)

        rhythm = ttk.LabelFrame(parent, text="Atomic rhythm patterns", padding=10)
        rhythm.pack(fill="both", expand=True, padx=10, pady=10)
        ttk.Label(rhythm, text="Tuplets: 64t = 3:2; 64t5 = 5:4; 64t7 = 7:4. Example mixed 5:4 group: 64t5,64t5,64t5,32t5").pack(anchor="w")
        self.atomic_rhythm_text = tk.Text(rhythm, height=7, wrap="none")
        self.atomic_rhythm_text.pack(fill="both", expand=True, pady=5)
        row2 = ttk.Frame(rhythm)
        row2.pack(fill="x")
        ttk.Label(row2, text="Atomic rhythm chance %").pack(side="left")
        ttk.Entry(row2, textvariable=self.new_var("atomic_rhythm_chance"), width=10).pack(side="left", padx=6)

        grace = ttk.LabelFrame(parent, text="Grace notes deducted from the main-note value", padding=10)
        grace.pack(fill="x", padx=10, pady=10)
        ttk.Checkbutton(grace, text="Enable grace notes", variable=self.new_var("grace_enabled", tk.BooleanVar)).grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        self.entry(grace, 1, "Grace value", "grace_value", width=12, note="Examples: 64, 64d, 64t, 64t5, 32d")
        self.entry(grace, 2, "Allowed grace motions", "grace_motions", width=28, note="Comma-separated, e.g. R,L,U,D")
        self.entry(grace, 3, "Number from", "grace_count_min")
        self.entry(grace, 4, "Number to", "grace_count_max")
        self.entry(grace, 5, "Grace chance %", "grace_chance", note="Applied only when enough main-note time remains")
        self.entry(
            grace, 6,
            "Do not use grace before main values",
            "grace_forbidden_main_values",
            width=28,
            note="Current main value; e.g. 16,32,64d,16t5",
        )
        self.entry(
            grace, 7,
            "Do not use grace after previous values",
            "grace_forbidden_previous_values",
            width=28,
            note="Previous main value; e.g. 32 blocks grace immediately after 32",
        )

        seed = ttk.LabelFrame(parent, text="Seed and output", padding=10)
        seed.pack(fill="x", padx=10, pady=10)
        self.entry(seed, 0, "Seed", "seed_spec", width=20, note="blank=random; 7=one file; 2-8=seven files")

    def number(self, key: str, kind=float):
        try:
            return kind(str(self.vars[key].get()).strip())
        except ValueError as exc:
            raise ValueError(f"{key.replace('_', ' ').title()} must be a valid number.") from exc

    def get_config(self) -> dict[str, Any]:
        c = {
            "title": str(self.vars["title"].get()).strip() or "matrix_melody",
            "time_signature_numerator": self.number("time_signature_numerator", int),
            "time_signature_denominator": self.number("time_signature_denominator", int),
            "measures": self.number("measures", int),
            "tempo_bpm": self.number("tempo_bpm", float),
            "midi_program": self.number("midi_program", int),
            "midi_channel": self.number("midi_channel", int),
            "low_note": self.number("low_note", int),
            "high_note": self.number("high_note", int),
            "velocity_low": self.number("velocity_low", int),
            "velocity_high": self.number("velocity_high", int),
            "start_note": self.number("start_note", int),
            "pitch_repetition_max": self.number("pitch_repetition_max", int),
            "pitch_repetition_chance": self.number("pitch_repetition_chance", float),
            "backward_motion_chance": self.number("backward_motion_chance", float),
            "motion_weights": {},
            "motion_max_runs": {},
            "rhythm_motion_rules": self.rhythm_motion_rules_text.get("1.0", "end").strip(),
            "atomic_motion_chance": self.number("atomic_motion_chance", float),
            "atomic_motion_patterns": self.atomic_motion_text.get("1.0", "end").strip(),
            "rest_min": str(self.vars["rest_min"].get()),
            "rest_max": str(self.vars["rest_max"].get()),
            "rest_chance": self.number("rest_chance", float),
            "rhythm_weights": {},
            "rhythm_group_min": {},
            "rhythm_group_max": {},
            "tuplet_mode": str(self.vars["tuplet_mode"].get()),
            "tuplet_full_beat_only": bool(self.vars["tuplet_full_beat_only"].get()),
            "tuplet_base_min": str(self.vars["tuplet_base_min"].get()),
            "tuplet_base_max": str(self.vars["tuplet_base_max"].get()),
            "tuplet_ratios": str(self.vars["tuplet_ratios"].get()).strip(),
            "tuplet_chance": self.number("tuplet_chance", float),
            "atomic_rhythm_chance": self.number("atomic_rhythm_chance", float),
            "atomic_rhythm_patterns": self.atomic_rhythm_text.get("1.0", "end").strip(),
            "grace_enabled": bool(self.vars["grace_enabled"].get()),
            "grace_value": str(self.vars["grace_value"].get()).strip(),
            "grace_motions": str(self.vars["grace_motions"].get()).strip(),
            "grace_count_min": self.number("grace_count_min", int),
            "grace_count_max": self.number("grace_count_max", int),
            "grace_chance": self.number("grace_chance", float),
            "grace_forbidden_main_values": str(self.vars["grace_forbidden_main_values"].get()).strip(),
            "grace_forbidden_previous_values": str(self.vars["grace_forbidden_previous_values"].get()).strip(),
            "seed_spec": str(self.vars["seed_spec"].get()).strip(),
        }
        for name in MOTION_NAMES:
            c["motion_weights"][name] = float(self.motion_weights[name].get() or 0) if self.motion_enabled[name].get() else 0.0
            c["motion_max_runs"][name] = int(self.motion_runs[name].get() or 0)
        for name in RHYTHM_NAMES:
            c["rhythm_weights"][name] = float(self.rhythm_weights[name].get() or 0) if self.rhythm_enabled[name].get() else 0.0
            c["rhythm_group_min"][name] = int(self.rhythm_mins[name].get() or 1)
            c["rhythm_group_max"][name] = int(self.rhythm_maxs[name].get() or 1)
        self.validate_config(c)
        return c

    def validate_config(self, c: dict[str, Any]) -> None:
        if c["time_signature_numerator"] < 1 or c["time_signature_denominator"] not in (16, 8, 4, 2):
            raise ValueError("Invalid time signature.")
        if c["measures"] < 1:
            raise ValueError("Measures must be at least 1.")
        if not 0 <= c["low_note"] < c["high_note"] <= 127:
            raise ValueError("MIDI range must satisfy 0 <= low < high <= 127.")
        if c["start_note"] != 0 and not c["low_note"] <= c["start_note"] <= c["high_note"]:
            raise ValueError("Start note must be 0 or inside the MIDI range.")
        if not 1 <= c["velocity_low"] <= c["velocity_high"] <= 127:
            raise ValueError("Velocity must satisfy 1 <= from <= to <= 127.")
        if not 1 <= c["midi_channel"] <= 16 or not 0 <= c["midi_program"] <= 127:
            raise ValueError("Invalid MIDI channel or programme.")
        if c["pitch_repetition_max"] < 1:
            raise ValueError("Maximum pitch repetition must be at least 1.")
        percentage_keys = ("pitch_repetition_chance", "backward_motion_chance", "atomic_motion_chance", "rest_chance", "tuplet_chance", "atomic_rhythm_chance", "grace_chance")
        for key in percentage_keys:
            if not 0 <= c[key] <= 100:
                raise ValueError(f"{key.replace('_', ' ').title()} must be between 0 and 100.")
        if sum(c["motion_weights"].values()) <= 0 and not c["atomic_motion_patterns"]:
            raise ValueError("Enable at least one motion or provide an atomic motion pattern.")
        if c["tuplet_mode"] != "Tuplets only" and sum(c["rhythm_weights"].values()) <= 0 and not c["atomic_rhythm_patterns"]:
            raise ValueError("Enable a rhythm, choose Tuplets only, or provide an atomic rhythm pattern.")
        for name in RHYTHM_NAMES:
            if c["rhythm_group_min"][name] < 1 or c["rhythm_group_max"][name] < c["rhythm_group_min"][name]:
                raise ValueError(f"Invalid group minimum/maximum for {name}.")
        ratios = [int(x.strip()) for x in c["tuplet_ratios"].split(",") if x.strip()]
        if c["tuplet_mode"] != "Off" and (not ratios or any(x < 2 for x in ratios)):
            raise ValueError("Tuplet ratios must be integers of 2 or more.")
        parse_rhythm_motion_rules(c["rhythm_motion_rules"])
        for line in c["atomic_motion_patterns"].splitlines():
            if line.strip():
                parse_motion_pattern(line)
        for line in c["atomic_rhythm_patterns"].splitlines():
            if line.strip():
                parse_rhythm_pattern(line)
        if c["grace_enabled"]:
            parse_rhythm_token(c["grace_value"])
            parse_motion_pattern(c["grace_motions"])
            if c["grace_count_min"] < 1 or c["grace_count_max"] < c["grace_count_min"]:
                raise ValueError("Invalid grace-note count range.")
            for field_name in (
                "grace_forbidden_main_values",
                "grace_forbidden_previous_values",
            ):
                for item in c[field_name].split(","):
                    if item.strip():
                        parse_rhythm_token(item.strip())
        parse_seed_spec(c["seed_spec"])

    def set_config(self, config: dict[str, Any]) -> None:
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        for key, value in config.items():
            if key in {"motion_weights", "motion_max_runs", "rhythm_weights", "rhythm_group_min", "rhythm_group_max"} and isinstance(value, dict):
                merged[key].update(value)
            else:
                merged[key] = value
        for key, var in self.vars.items():
            if key in merged:
                var.set(merged[key])
        for name in MOTION_NAMES:
            weight = merged["motion_weights"].get(name, 0)
            self.motion_enabled[name].set(float(weight) > 0)
            self.motion_weights[name].set(str(weight))
            self.motion_runs[name].set(str(merged["motion_max_runs"].get(name, 0)))
        for name in RHYTHM_NAMES:
            weight = merged["rhythm_weights"].get(name, 0)
            self.rhythm_enabled[name].set(float(weight) > 0)
            self.rhythm_weights[name].set(str(weight))
            self.rhythm_mins[name].set(str(merged["rhythm_group_min"].get(name, 1)))
            self.rhythm_maxs[name].set(str(merged["rhythm_group_max"].get(name, 1)))
        self.rhythm_motion_rules_text.delete("1.0", "end")
        self.rhythm_motion_rules_text.insert(
            "1.0",
            merged.get("rhythm_motion_rules", ""),
        )
        self.atomic_motion_text.delete("1.0", "end")
        self.atomic_motion_text.insert("1.0", merged.get("atomic_motion_patterns", ""))
        self.atomic_rhythm_text.delete("1.0", "end")
        self.atomic_rhythm_text.insert("1.0", merged.get("atomic_rhythm_patterns", ""))
        self.status.set("Configuration loaded.")

    def validate_only(self) -> None:
        try:
            c = self.get_config()
            count = len(parse_seed_spec(c["seed_spec"]))
            messagebox.showinfo("Valid", f"Valid configuration. It will generate {count} MIDI file(s).")
        except Exception as exc:
            messagebox.showerror("Invalid configuration", str(exc))

    def generate(self) -> None:
        try:
            c = self.get_config()
            seeds = parse_seed_spec(c["seed_spec"])
            folder = filedialog.askdirectory(
                title="Choose output folder",
                initialdir=str(self.last_output_folder),
            )
            if not folder:
                return
            output_folder = Path(folder)
            self.last_output_folder = output_folder
            save_last_output_folder(output_folder)
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", c["title"]).strip("_") or "matrix_melody"
            names = []
            for seed in seeds:
                events = MelodyGenerator(c, seed).generate()
                suffix = f"_seed_{seed}" if seed is not None else ""
                clip_name = f"{safe}{suffix}"
                output = output_folder / f"{clip_name}.mid"
                file_config = dict(c)
                file_config["midi_clip_name"] = clip_name
                write_midi(events, output, file_config)
                names.append(output.name)
            self.status.set(f"Created {len(names)} MIDI file(s) in {folder}.")
            messagebox.showinfo("Generation complete", self.status.get() + "\n\n" + "\n".join(names))
        except Exception as exc:
            messagebox.showerror("Cannot generate", str(exc))

    def save_preset(self) -> None:
        try:
            c = self.get_config()
            path = filedialog.asksaveasfilename(
                defaultextension=".py",
                filetypes=[("Python preset", "*.py"), ("JSON preset", "*.json")],
                initialfile=f"{c['title']}_preset.py",
            )
            if not path:
                return
            output = Path(path)
            if output.suffix.lower() == ".json":
                output.write_text(json.dumps(c, indent=4), encoding="utf-8")
            else:
                output.write_text('"""ALGOCOMPO GUI v2 preset."""\n\nCONFIG = ' + repr(c) + "\n", encoding="utf-8")
            self.status.set(f"Saved preset: {output}")
        except Exception as exc:
            messagebox.showerror("Cannot save preset", str(exc))

    def load_preset(self) -> None:
        try:
            path = filedialog.askopenfilename(filetypes=[("ALGOCOMPO presets", "*.py *.json"), ("All files", "*.*")])
            if not path:
                return
            source = Path(path).read_text(encoding="utf-8")
            if Path(path).suffix.lower() == ".json":
                config = json.loads(source)
            else:
                tree = ast.parse(source, filename=path)
                node = None
                for item in tree.body:
                    if isinstance(item, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "CONFIG" for target in item.targets):
                        node = item.value
                if node is None:
                    raise ValueError("Python preset must contain CONFIG = {...}.")
                config = ast.literal_eval(node)
            if not isinstance(config, dict):
                raise ValueError("Preset CONFIG must be a dictionary.")
            self.set_config(config)
            self.get_config()
            self.status.set(f"Loaded preset: {path}")
        except Exception as exc:
            messagebox.showerror("Cannot load preset", str(exc))


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
