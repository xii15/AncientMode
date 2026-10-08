"""melody_morse_01.py

Creates a Matrix-generated melody whose durations and rests come from Morse code.
Requires matrix.py and morse_rhythm.py in the same folder.
"""

from pathlib import Path
import math
import random
import struct

from matrix import apply_motion, is_leap, motion_value
from morse_rhythm import morse_rhythm, normalise_text

# -----------------------------------------------------------------------------
# PIECE SETTINGS
# -----------------------------------------------------------------------------
TEXT = "JESUS VICTORIOUS"
OUTPUT_FILE = Path(__file__).resolve().parent / "melody_morse_02b.mid"

LOW_NOTE = 75
HIGH_NOTE = 90
START_NOTE = 85

# Ordinary motions occupy 80% of selections; double leaps occupy 20%.
ORDINARY_MOTIONS = ("R", "L", "U", "D")
COMPOUND_LEAPS = ("U2", "D2", "U3", "D3")
COMPOUND_LEAP_PROBABILITY = 0.10 # OVO
NO_CONSECUTIVE_LEAPS = False

TEMPO_BPM = 120
TIME_SIGNATURE = (5, 4)
VELOCITY_MIN = 38
VELOCITY_MAX = 43
PROGRAM = 73            # General MIDI Flute, zero-based programme number
CHANNEL = 0
TICKS_PER_BEAT = 480
RANDOM_SEED = 777       # None gives a different melody on every run.

# Quarter-note beat values used by morse_rhythm().
SHORT_NOTE_BEATS = 0.5       # eighth note
LONG_NOTE_BEATS = 1.0        # quarter note
SYMBOL_REST_BEATS = 0      # eighth rest
LETTER_REST_BEATS = 0.5      # half rest
WORD_REST_BEATS = 1.0        # dotted-half rest


def available_motions(note, previous_motion):
    """Return valid ordinary and compound motions for the current note."""
    previous_was_leap = (
        previous_motion is not None
        and is_leap(previous_motion)
    )

    ordinary = []
    compound = []

    for motion in ORDINARY_MOTIONS:
        if (
            NO_CONSECUTIVE_LEAPS
            and previous_was_leap
            and is_leap(motion)
        ):
            continue

        result = note + motion_value(motion)

        if LOW_NOTE <= result <= HIGH_NOTE:
            ordinary.append(motion)

    for motion in COMPOUND_LEAPS:
        if NO_CONSECUTIVE_LEAPS and previous_was_leap:
            continue

        result = note + motion_value(motion)

        if LOW_NOTE <= result <= HIGH_NOTE:
            compound.append(motion)

    return ordinary, compound


def choose_motion(note, previous_motion, rng):
    ordinary, compound = available_motions(
        note,
        previous_motion
    )

    if not ordinary and not compound:
        raise RuntimeError(
            f"No legal Matrix motion is available "
            f"from MIDI note {note}."
        )

    if ordinary and compound:
        if rng.random() < COMPOUND_LEAP_PROBABILITY:
            pool = compound
        else:
            pool = ordinary
    else:
        pool = ordinary or compound

    return rng.choice(pool)


def generate_pitches(number_of_notes, rng):
    """Generate exactly one pitch for every Morse note event."""
    if not LOW_NOTE <= START_NOTE <= HIGH_NOTE:
        raise ValueError("START_NOTE must be inside LOW_NOTE and HIGH_NOTE.")

    pitches = [START_NOTE]
    motions = []
    current = START_NOTE
    previous_motion = None

    for _ in range(number_of_notes - 1):
        motion = choose_motion(current, previous_motion, rng)
        current = apply_motion(current, motion, LOW_NOTE, HIGH_NOTE)
        pitches.append(current)
        motions.append(motion)
        previous_motion = motion

    return pitches, motions


def combine_pitch_and_rhythm(rhythm_events, pitches, rng):
    """Attach one Matrix pitch to every Morse note; rests consume no pitch."""
    timed_events = []
    pitch_index = 0
    for kind, duration, source in rhythm_events:
        if kind == "rest":
            timed_events.append(("rest", duration, None, None, source))
        else:
            velocity = rng.randint(VELOCITY_MIN, VELOCITY_MAX)
            timed_events.append(("note", duration, pitches[pitch_index], velocity, source))
            pitch_index += 1
    if pitch_index != len(pitches):
        raise AssertionError("Not all generated pitches were used.")
    return timed_events


# -----------------------------------------------------------------------------
# MIDI EXPORT
# -----------------------------------------------------------------------------
def variable_length(value):
    buffer = value & 0x7F
    output = bytearray([buffer])
    while value > 0x7F:
        value >>= 7
        output.insert(0, (value & 0x7F) | 0x80)
    return bytes(output)


def meta_event(delta, event_type, data):
    return variable_length(delta) + bytes([0xFF, event_type]) + variable_length(len(data)) + data


def midi_message(delta, status, *data):
    return variable_length(delta) + bytes([status, *data])


def write_midi(events, output_file):
    numerator, denominator = TIME_SIGNATURE
    if denominator <= 0 or denominator & (denominator - 1):
        raise ValueError("Time-signature denominator must be a power of two.")

    track = bytearray()
    track += meta_event(0, 0x03, b"JESUS VICTORIOUS morse") # ---------------------------------------------------------NAME
    microseconds_per_quarter = round(60_000_000 / TEMPO_BPM)
    track += meta_event(0, 0x51, microseconds_per_quarter.to_bytes(3, "big"))
    track += meta_event(0, 0x58, bytes([numerator, int(math.log2(denominator)), 24, 8]))
    track += midi_message(0, 0xC0 | CHANNEL, PROGRAM)

    pending_ticks = 0
    for kind, beats, pitch, velocity, _source in events:
        ticks = round(beats * TICKS_PER_BEAT)
        if kind == "rest":
            pending_ticks += ticks
        else:
            track += midi_message(pending_ticks, 0x90 | CHANNEL, pitch, velocity)
            track += midi_message(ticks, 0x80 | CHANNEL, pitch, 0)
            pending_ticks = 0

    track += meta_event(pending_ticks, 0x2F, b"")
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, TICKS_PER_BEAT)
    track_chunk = b"MTrk" + struct.pack(">I", len(track)) + bytes(track)
    output_file.write_bytes(header + track_chunk)
    return output_file.resolve()

    
def validate(rhythm_events, pitches, motions, timed_events):
    note_event_count = sum(
        kind == "note"
        for kind, _, _ in rhythm_events
    )

    assert len(pitches) == note_event_count
    assert len(motions) == max(0, note_event_count - 1)

    assert all(
        LOW_NOTE <= pitch <= HIGH_NOTE
        for pitch in pitches
    )

    if NO_CONSECUTIVE_LEAPS:
        assert not any(
            is_leap(first_motion)
            and is_leap(second_motion)
            for first_motion, second_motion
            in zip(motions, motions[1:])
        )

    assert all(
        VELOCITY_MIN <= velocity <= VELOCITY_MAX
        for kind, _, _, velocity, _ in timed_events
        if kind == "note"
    )

def main():
    rng = random.Random(RANDOM_SEED)
    rhythm_events = morse_rhythm(
        TEXT,
        short_note=SHORT_NOTE_BEATS,
        long_note=LONG_NOTE_BEATS,
        symbol_rest=SYMBOL_REST_BEATS,
        letter_rest=LETTER_REST_BEATS,
        word_rest=WORD_REST_BEATS,
    )
    note_count = sum(kind == "note" for kind, _, _ in rhythm_events)
    pitches, motions = generate_pitches(note_count, rng)
    timed_events = combine_pitch_and_rhythm(rhythm_events, pitches, rng)
    validate(rhythm_events, pitches, motions, timed_events)
    output = write_midi(timed_events, OUTPUT_FILE)

    total_beats = sum(duration for _, duration, _ in rhythm_events)
    print("Text:", normalise_text(TEXT))
    print("Created:", output)
    print("Morse note events:", note_count)
    print("Total quarter-note beats:", total_beats)
    print("Pitch range used:", min(pitches), "to", max(pitches))
    print("Motions:", ",".join(motions))


if __name__ == "__main__":
    main()
