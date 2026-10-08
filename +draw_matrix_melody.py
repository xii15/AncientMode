"""draw_matrix_melody.py

Resizable Tkinter contour-drawing tool for ALGOCOMPO.

Draw a contour with the mouse. The vertical axis represents MIDI pitch and the
horizontal axis represents musical time. The programme samples the contour at
note onsets, then chooses legal Matrix motions whose destinations follow the
contour as closely as possible.

Requires matrix.py in the same folder.
Uses only Python's standard library.
"""

from pathlib import Path
import math
import random
import struct
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from matrix import motion_value


# =============================================================================
# DEFAULTS
# =============================================================================

APP_TITLE = "AncientMode Matrix Contour Drawer"
DEFAULT_WIDTH = 1500
DEFAULT_HEIGHT = 900
TICKS_PER_BEAT = 960

MOTIONS = ("R", "L", "U", "D", "U2", "D2")
MOTION_WEIGHTS = (24, 24, 15, 15, 8, 8)
NOTE_DURATIONS = (0.25, 0.50, 0.75, 1.00)
NOTE_DURATION_WEIGHTS = (38, 37, 15, 10)
SNIPPET_SIZES = (2, 3, 4, 5)
SNIPPET_SIZE_WEIGHTS = (3, 4, 4, 3)


# =============================================================================
# MIDI HELPERS
# =============================================================================

def variable_length(value):
    if value < 0:
        raise ValueError("MIDI delta time cannot be negative.")
    buffer = value & 0x7F
    output = bytearray([buffer])
    while value > 0x7F:
        value >>= 7
        output.insert(0, (value & 0x7F) | 0x80)
    return bytes(output)


def meta_event(delta, event_type, data):
    return (
        variable_length(delta)
        + bytes([0xFF, event_type])
        + variable_length(len(data))
        + data
    )


def midi_message(delta, status, *data):
    return variable_length(delta) + bytes([status, *data])


def write_midi(events, output_path, tempo, numerator, denominator,
               velocity, programme=73, channel=0):
    if denominator <= 0 or denominator & (denominator - 1):
        raise ValueError("Time-signature denominator must be a power of two.")

    track = bytearray()
    track += meta_event(0, 0x03, b"Drawn Matrix Melody")
    microseconds_per_quarter = round(60_000_000 / tempo)
    track += meta_event(0, 0x51, microseconds_per_quarter.to_bytes(3, "big"))
    track += meta_event(
        0, 0x58,
        bytes([numerator, int(math.log2(denominator)), 24, 8])
    )
    track += midi_message(0, 0xC0 | channel, programme)

    pending_ticks = 0
    for event in events:
        ticks = round(event["duration"] * TICKS_PER_BEAT)
        if event["kind"] == "rest":
            pending_ticks += ticks
        else:
            pitch = event["pitch"]
            track += midi_message(
                pending_ticks, 0x90 | channel, pitch, velocity
            )
            track += midi_message(ticks, 0x80 | channel, pitch, 0)
            pending_ticks = 0

    track += meta_event(pending_ticks, 0x2F, b"")
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, TICKS_PER_BEAT)
    track_chunk = b"MTrk" + struct.pack(">I", len(track)) + bytes(track)
    Path(output_path).write_bytes(header + track_chunk)


# =============================================================================
# APPLICATION
# =============================================================================

class MatrixContourApp:
    LEFT_MARGIN = 72
    RIGHT_MARGIN = 24
    TOP_MARGIN = 48
    BOTTOM_MARGIN = 46

    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry(f"{DEFAULT_WIDTH}x{DEFAULT_HEIGHT}")
        root.minsize(900, 600)

        self.strokes = []
        self.current_stroke = []
        self.generated_events = []
        self.generated_path = []

        self.low_note = tk.IntVar(value=79)
        self.high_note = tk.IntVar(value=93)
        self.start_note = tk.IntVar(value=85)
        self.measures = tk.IntVar(value=6)
        self.numerator = tk.IntVar(value=3)
        self.denominator = tk.IntVar(value=4)
        self.tempo = tk.IntVar(value=60)
        self.velocity = tk.IntVar(value=120)
        self.rest_probability = tk.DoubleVar(value=0.35)
        self.contour_strength = tk.DoubleVar(value=0.80)
        self.return_probability = tk.DoubleVar(value=0.05)
        self.lookback = tk.IntVar(value=3)
        self.seed_text = tk.StringVar(value="")
        self.status = tk.StringVar(
            value="Draw from left to right. Then click Preview or Save MIDI."
        )

        self.build_interface()
        self.root.after(100, self.redraw)

    # -------------------------------------------------------------------------
    # Interface
    # -------------------------------------------------------------------------

    def build_interface(self):
        controls = ttk.Frame(self.root, padding=8)
        controls.pack(side="top", fill="x")

        fields = [
            ("Low", self.low_note, 5),
            ("High", self.high_note, 5),
            ("Start", self.start_note, 5),
            ("Measures", self.measures, 5),
            ("TS top", self.numerator, 4),
            ("TS bottom", self.denominator, 4),
            ("Tempo", self.tempo, 5),
            ("Velocity", self.velocity, 5),
            ("Rest chance", self.rest_probability, 6),
            ("Contour", self.contour_strength, 6),
            ("Return chance", self.return_probability, 6),
            ("Lookback", self.lookback, 4),
            ("Seed", self.seed_text, 8),
        ]

        for column, (label, variable, width) in enumerate(fields):
            ttk.Label(controls, text=label).grid(
                row=0, column=column, padx=(0, 3), sticky="w"
            )
            entry = ttk.Entry(controls, textvariable=variable, width=width)
            entry.grid(row=1, column=column, padx=(0, 8), sticky="w")

        buttons = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        buttons.pack(side="top", fill="x")

        ttk.Button(buttons, text="Undo stroke", command=self.undo).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(buttons, text="Clear", command=self.clear).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(buttons, text="Redraw grid", command=self.redraw).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(buttons, text="Preview path", command=self.preview).pack(
            side="left", padx=(12, 6)
        )
        ttk.Button(buttons, text="Save MIDI", command=self.save_midi).pack(
            side="left", padx=(0, 6)
        )

        ttk.Label(
            buttons,
            text="Motions: R,L,U,D,U2,D2 | values: 16th,8th,dotted 8th,quarter",
        ).pack(side="right")

        self.canvas = tk.Canvas(
            self.root,
            background="#fbfbf8",
            highlightthickness=0,
            cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self.start_stroke)
        self.canvas.bind("<B1-Motion>", self.extend_stroke)
        self.canvas.bind("<ButtonRelease-1>", self.finish_stroke)

        status_bar = ttk.Label(
            self.root, textvariable=self.status, anchor="w", padding=(8, 5)
        )
        status_bar.pack(side="bottom", fill="x")

    # -------------------------------------------------------------------------
    # Geometry and conversion
    # -------------------------------------------------------------------------

    def plot_bounds(self):
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        return (
            self.LEFT_MARGIN,
            self.TOP_MARGIN,
            width - self.RIGHT_MARGIN,
            height - self.BOTTOM_MARGIN,
        )

    def settings(self):
        low = self.low_note.get()
        high = self.high_note.get()
        start = self.start_note.get()
        measures = self.measures.get()
        numerator = self.numerator.get()
        denominator = self.denominator.get()
        tempo = self.tempo.get()
        velocity = self.velocity.get()
        rest_probability = self.rest_probability.get()
        contour_strength = self.contour_strength.get()
        return_probability = self.return_probability.get()
        lookback = self.lookback.get()

        if not 0 <= low < high <= 127:
            raise ValueError("Pitch range must satisfy 0 <= Low < High <= 127.")
        if not low <= start <= high:
            raise ValueError("Start note must be inside the pitch range.")
        if measures < 1:
            raise ValueError("Measures must be at least 1.")
        if numerator < 1:
            raise ValueError("The time-signature numerator must be positive.")
        if denominator <= 0 or denominator & (denominator - 1):
            raise ValueError("The time-signature denominator must be 1,2,4,8,16...")
        if not 20 <= tempo <= 400:
            raise ValueError("Tempo must be between 20 and 400 BPM.")
        if not 1 <= velocity <= 127:
            raise ValueError("Velocity must be between 1 and 127.")
        if not 0.0 <= rest_probability <= 1.0:
            raise ValueError("Rest chance must be between 0.0 and 1.0.")
        if not 0.0 <= contour_strength <= 1.0:
            raise ValueError("Contour must be between 0.0 and 1.0.")
        if not 0.0 <= return_probability <= 1.0:
            raise ValueError("Return chance must be between 0.0 and 1.0.")
        if lookback < 1:
            raise ValueError("Lookback must be at least 1.")

        beats_per_measure = numerator * (4 / denominator)
        total_beats = measures * beats_per_measure
        return {
            "low": low,
            "high": high,
            "start": start,
            "measures": measures,
            "numerator": numerator,
            "denominator": denominator,
            "tempo": tempo,
            "velocity": velocity,
            "rest_probability": rest_probability,
            "contour_strength": contour_strength,
            "return_probability": return_probability,
            "lookback": lookback,
            "beats_per_measure": beats_per_measure,
            "total_beats": total_beats,
        }

    def beat_to_x(self, beat, total_beats):
        left, _top, right, _bottom = self.plot_bounds()
        return left + (beat / total_beats) * (right - left)

    def pitch_to_y(self, pitch, low, high):
        _left, top, _right, bottom = self.plot_bounds()
        fraction = (pitch - low) / (high - low)
        return bottom - fraction * (bottom - top)

    def y_to_pitch(self, y, low, high):
        _left, top, _right, bottom = self.plot_bounds()
        y = min(max(y, top), bottom)
        fraction = (bottom - y) / (bottom - top)
        return low + fraction * (high - low)

    # -------------------------------------------------------------------------
    # Drawing
    # -------------------------------------------------------------------------

    def start_stroke(self, event):
        left, top, right, bottom = self.plot_bounds()
        if left <= event.x <= right and top <= event.y <= bottom:
            self.current_stroke = [(event.x, event.y)]
            self.generated_events = []
            self.generated_path = []

    def extend_stroke(self, event):
        if not self.current_stroke:
            return
        left, top, right, bottom = self.plot_bounds()
        x = min(max(event.x, left), right)
        y = min(max(event.y, top), bottom)
        previous = self.current_stroke[-1]
        if abs(x - previous[0]) + abs(y - previous[1]) >= 2:
            self.current_stroke.append((x, y))
            self.canvas.create_line(
                previous[0], previous[1], x, y,
                fill="#2356a8", width=3, smooth=True,
                tags="user_stroke",
            )

    def finish_stroke(self, _event):
        if len(self.current_stroke) > 1:
            self.strokes.append(self.current_stroke)
            self.status.set(
                f"Stroke stored. Total strokes: {len(self.strokes)}."
            )
        self.current_stroke = []
        self.redraw()

    def undo(self):
        if self.strokes:
            self.strokes.pop()
        self.generated_events = []
        self.generated_path = []
        self.redraw()

    def clear(self):
        self.strokes.clear()
        self.current_stroke = []
        self.generated_events = []
        self.generated_path = []
        self.status.set("Canvas cleared.")
        self.redraw()

    def redraw(self):
        self.canvas.delete("all")
        try:
            settings = self.settings()
        except (ValueError, tk.TclError):
            return

        left, top, right, bottom = self.plot_bounds()
        low = settings["low"]
        high = settings["high"]
        total_beats = settings["total_beats"]
        beats_per_measure = settings["beats_per_measure"]

        self.canvas.create_rectangle(
            left, top, right, bottom, outline="#444444", width=1
        )

        # Pitch grid and labels.
        for pitch in range(low, high + 1):
            y = self.pitch_to_y(pitch, low, high)
            is_octave = pitch % 12 == 0
            colour = "#b9b9b9" if is_octave else "#e8e8e3"
            width = 1.5 if is_octave else 1
            self.canvas.create_line(left, y, right, y, fill=colour, width=width)
            if is_octave or pitch in (low, high):
                self.canvas.create_text(
                    left - 8, y, text=str(pitch), anchor="e", fill="#333333"
                )

        # Beat lines and stronger barlines.
        total_quarter_beats = int(math.ceil(total_beats))
        for beat in range(total_quarter_beats + 1):
            if beat > total_beats + 1e-9:
                break
            x = self.beat_to_x(beat, total_beats)
            measure_position = beat / beats_per_measure
            is_barline = abs(measure_position - round(measure_position)) < 1e-9
            self.canvas.create_line(
                x, top, x, bottom,
                fill="#858585" if is_barline else "#dddddd",
                width=2 if is_barline else 1,
            )

        # Measure numbers, including non-quarter denominators.
        for measure in range(settings["measures"]):
            beat = measure * beats_per_measure
            x = self.beat_to_x(beat, total_beats)
            self.canvas.create_text(
                x + 5, top - 20, text=str(measure + 1),
                anchor="w", fill="#333333"
            )

        # Repaint stored user strokes.
        for stroke in self.strokes:
            if len(stroke) > 1:
                flattened = [coordinate for point in stroke for coordinate in point]
                self.canvas.create_line(
                    *flattened,
                    fill="#2356a8", width=3, smooth=True,
                    tags="user_stroke",
                )

        # Generated Matrix path.
        if self.generated_path:
            points = []
            for beat, pitch in self.generated_path:
                points.extend([
                    self.beat_to_x(beat, total_beats),
                    self.pitch_to_y(pitch, low, high),
                ])
            if len(points) >= 4:
                self.canvas.create_line(
                    *points, fill="#d0442f", width=2, tags="generated"
                )
            radius = 3
            for index in range(0, len(points), 2):
                x, y = points[index], points[index + 1]
                self.canvas.create_oval(
                    x - radius, y - radius, x + radius, y + radius,
                    fill="#d0442f", outline="",
                )

        self.canvas.create_text(
            (left + right) / 2, bottom + 25,
            text="musical time", fill="#555555"
        )
        self.canvas.create_text(
            18, (top + bottom) / 2,
            text="MIDI pitch", angle=90, fill="#555555"
        )

    # -------------------------------------------------------------------------
    # Contour interpolation
    # -------------------------------------------------------------------------

    def contour_points(self):
        points = [point for stroke in self.strokes for point in stroke]
        if not points:
            raise ValueError("Draw at least one contour stroke first.")
        return sorted(points, key=lambda point: point[0])

    def target_pitch_at_beat(self, beat, settings, points):
        x = self.beat_to_x(beat, settings["total_beats"])
        if x <= points[0][0]:
            y = points[0][1]
        elif x >= points[-1][0]:
            y = points[-1][1]
        else:
            y = points[-1][1]
            for first, second in zip(points, points[1:]):
                if first[0] <= x <= second[0]:
                    if second[0] == first[0]:
                        y = second[1]
                    else:
                        fraction = (x - first[0]) / (second[0] - first[0])
                        y = first[1] + fraction * (second[1] - first[1])
                    break
        return self.y_to_pitch(y, settings["low"], settings["high"])

    # -------------------------------------------------------------------------
    # Melody generation
    # -------------------------------------------------------------------------

    def rng(self):
        text = self.seed_text.get().strip()
        if text == "":
            return random.Random()
        try:
            seed = int(text)
        except ValueError:
            seed = text
        return random.Random(seed)

    def choose_duration(self, remaining, rng):
        values = []
        weights = []
        for duration, weight in zip(NOTE_DURATIONS, NOTE_DURATION_WEIGHTS):
            if duration <= remaining + 1e-9:
                values.append(duration)
                weights.append(weight)
        if not values:
            return None
        return rng.choices(values, weights=weights, k=1)[0]

    def choose_motion(self, current, target, history, settings, rng):
        candidates = []
        for motion, base_weight in zip(MOTIONS, MOTION_WEIGHTS):
            destination = current + motion_value(motion)
            if not settings["low"] <= destination <= settings["high"]:
                continue

            distance = abs(destination - target)
            closeness = 1.0 / (1.0 + distance)
            contour_factor = (
                (1.0 - settings["contour_strength"])
                + settings["contour_strength"] * closeness * 12.0
            )

            recent = history[-settings["lookback"]:]
            if destination in recent:
                return_factor = settings["return_probability"]
            else:
                return_factor = 1.0

            effective_weight = base_weight * contour_factor * return_factor
            if effective_weight > 0:
                candidates.append((motion, destination, effective_weight))

        if not candidates:
            raise RuntimeError(f"No legal motion from MIDI note {current}.")

        chosen = rng.choices(
            candidates,
            weights=[item[2] for item in candidates],
            k=1,
        )[0]
        return chosen[0], chosen[1]

    def generate(self):
        settings = self.settings()
        points = self.contour_points()
        rng = self.rng()
        events = []
        path = []
        elapsed = 0.0
        current = settings["start"]
        history = [current]
        first_note = True

        while elapsed < settings["total_beats"] - 1e-9:
            snippet_size = rng.choices(
                SNIPPET_SIZES, weights=SNIPPET_SIZE_WEIGHTS, k=1
            )[0]

            for _ in range(snippet_size):
                remaining = settings["total_beats"] - elapsed
                duration = self.choose_duration(remaining, rng)
                if duration is None:
                    break

                if first_note:
                    first_note = False
                else:
                    target = self.target_pitch_at_beat(elapsed, settings, points)
                    _motion, current = self.choose_motion(
                        current, target, history, settings, rng
                    )
                    history.append(current)

                events.append({
                    "kind": "note",
                    "duration": duration,
                    "pitch": current,
                })
                path.append((elapsed, current))
                elapsed += duration

                if elapsed >= settings["total_beats"] - 1e-9:
                    break

            if elapsed >= settings["total_beats"] - 1e-9:
                break

            remaining = settings["total_beats"] - elapsed
            if (
                rng.random() < settings["rest_probability"]
                and 0.5 <= remaining + 1e-9
            ):
                events.append({"kind": "rest", "duration": 0.5, "pitch": None})
                elapsed += 0.5

        if elapsed < settings["total_beats"] - 1e-9:
            events.append({
                "kind": "rest",
                "duration": settings["total_beats"] - elapsed,
                "pitch": None,
            })

        self.generated_events = events
        self.generated_path = path
        return settings, events, path

    def preview(self):
        try:
            settings, events, path = self.generate()
            self.redraw()
            self.status.set(
                f"Preview: {len(path)} notes, {settings['measures']} measures. "
                "Blue = drawing, red = Matrix melody."
            )
        except Exception as error:
            messagebox.showerror("Cannot generate preview", str(error))

    def save_midi(self):
        try:
            settings, events, path = self.generate()
            self.redraw()
            default_path = Path(__file__).resolve().parent / "drawn_matrix_melody.mid"
            output = filedialog.asksaveasfilename(
                title="Save Matrix melody",
                initialdir=str(default_path.parent),
                initialfile=default_path.name,
                defaultextension=".mid",
                filetypes=[("MIDI files", "*.mid")],
            )
            if not output:
                return
            write_midi(
                events,
                output,
                settings["tempo"],
                settings["numerator"],
                settings["denominator"],
                settings["velocity"],
            )
            self.status.set(f"Saved {len(path)} notes to {output}")
            messagebox.showinfo("MIDI saved", f"Created:\n{output}")
        except Exception as error:
            messagebox.showerror("Cannot save MIDI", str(error))


def main():
    root = tk.Tk()
    app = MatrixContourApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
