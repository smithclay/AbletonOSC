from typing import Tuple, Any
from .handler import AbletonOSCHandler
import Live


class ArrangementClipHandler(AbletonOSCHandler):
    def __init__(self, manager):
        super().__init__(manager)
        self.class_identifier = "arrangement_clip"

    def init_api(self):
        def create_arrangement_clip_callback(func, *args, pass_clip_index=False):
            """
            Creates a callback that expects:
              (track_index, clip_index, *args)

            Resolves clip via track.arrangement_clips[clip_index].
            """
            def arrangement_clip_callback(params: Tuple[Any]) -> Tuple:
                track_index, clip_index = int(params[0]), int(params[1])
                track = self.song.tracks[track_index]
                if not hasattr(track, 'arrangement_clips'):
                    raise RuntimeError("arrangement_clips not available (requires Live 11+)")
                clip = track.arrangement_clips[clip_index]
                if pass_clip_index:
                    rv = func(clip, *args, tuple(params[0:]))
                else:
                    rv = func(clip, *args, tuple(params[2:]))

                if rv is not None:
                    return (track_index, clip_index, *rv)

            return arrangement_clip_callback

        # -------------------------------------------------------------------
        # Clip-level properties
        # -------------------------------------------------------------------
        properties_r = [
            "start_time",
            "end_time",
            "length",
            "is_midi_clip",
            "color",
        ]
        properties_rw = [
            "name",
            "loop_start",
            "loop_end",
            "start_marker",
            "end_marker",
            "looping",
        ]

        for prop in properties_r + properties_rw:
            self.osc_server.add_handler("/live/arrangement_clip/get/%s" % prop,
                                        create_arrangement_clip_callback(self._get_property, prop))
            self.osc_server.add_handler("/live/arrangement_clip/start_listen/%s" % prop,
                                        create_arrangement_clip_callback(self._start_listen, prop, pass_clip_index=True))
            self.osc_server.add_handler("/live/arrangement_clip/stop_listen/%s" % prop,
                                        create_arrangement_clip_callback(self._stop_listen, prop, pass_clip_index=True))
        for prop in properties_rw:
            self.osc_server.add_handler("/live/arrangement_clip/set/%s" % prop,
                                        create_arrangement_clip_callback(self._set_property, prop))

        # -------------------------------------------------------------------
        # MIDI Notes — same flat-tuple format as /live/clip/ notes
        # -------------------------------------------------------------------
        def clip_get_notes(clip, params: Tuple[Any] = ()):
            if len(params) == 4:
                pitch_start, pitch_span, time_start, time_span = params
            elif len(params) == 0:
                pitch_start, pitch_span, time_start, time_span = 0, 127, -8192, 16384
            else:
                raise ValueError("Invalid number of arguments for get/notes. Either 0 or 4 arguments must be passed.")
            notes = clip.get_notes_extended(pitch_start, pitch_span, time_start, time_span)
            all_note_attributes = []
            for note in notes:
                all_note_attributes += [note.pitch, note.start_time, note.duration, note.velocity, note.mute]
            return tuple(all_note_attributes)

        def clip_add_notes(clip, params: Tuple[Any] = ()):
            notes = []
            for offset in range(0, len(params), 5):
                pitch, start_time, duration, velocity, mute = params[offset:offset + 5]
                note = Live.Clip.MidiNoteSpecification(start_time=start_time,
                                                       duration=duration,
                                                       pitch=pitch,
                                                       velocity=velocity,
                                                       mute=mute)
                notes.append(note)
            clip.add_new_notes(tuple(notes))

        def clip_remove_notes(clip, params: Tuple[Any] = ()):
            if len(params) == 4:
                pitch_start, pitch_span, time_start, time_span = params
            elif len(params) == 0:
                pitch_start, pitch_span, time_start, time_span = 0, 127, -8192, 16384
            else:
                raise ValueError("Invalid number of arguments for remove/notes. Either 0 or 4 arguments must be passed.")
            clip.remove_notes_extended(pitch_start, pitch_span, time_start, time_span)

        self.osc_server.add_handler("/live/arrangement_clip/get/notes", create_arrangement_clip_callback(clip_get_notes))
        self.osc_server.add_handler("/live/arrangement_clip/add/notes", create_arrangement_clip_callback(clip_add_notes))
        self.osc_server.add_handler("/live/arrangement_clip/remove/notes", create_arrangement_clip_callback(clip_remove_notes))

        # -------------------------------------------------------------------
        # Track-level arrangement clip operations
        # Uses standalone callbacks with track_index prefix.
        # -------------------------------------------------------------------

        def create_arrangement_clip(params: Tuple[Any]):
            """Create a MIDI clip in the arrangement view.
            Params: track_id, start_time, length

            Creates in an empty session clip slot, duplicates to arrangement,
            then cleans up the session clip.
            """
            track_index = int(params[0])
            start_time = float(params[1])
            length = float(params[2])
            track = self.song.tracks[track_index]

            empty_slot = None
            for i, slot in enumerate(track.clip_slots):
                if not slot.has_clip:
                    empty_slot = (i, slot)
                    break
            if empty_slot is None:
                raise RuntimeError("No empty clip slot available for arrangement clip creation")
            slot_index, slot = empty_slot
            slot.create_clip(length)
            clip = slot.clip
            track.duplicate_clip_to_arrangement(clip, start_time)
            slot.delete_clip()

            for i, ac in enumerate(track.arrangement_clips):
                if abs(ac.start_time - start_time) < 0.001:
                    return (track_index, i)
            return (track_index, -1)

        def delete_arrangement_clip(params: Tuple[Any]):
            """Delete an arrangement clip.
            Params: track_id, clip_id
            """
            track_index = int(params[0])
            clip_index = int(params[1])
            track = self.song.tracks[track_index]
            clip = track.arrangement_clips[clip_index]
            track.delete_clip(clip)

        def duplicate_to_arrangement(params: Tuple[Any]):
            """Copy a session clip to the arrangement at dest_time.
            Params: track_id, clip_slot_id, dest_time
            """
            track_index = int(params[0])
            clip_slot_id = int(params[1])
            dest_time = float(params[2])
            track = self.song.tracks[track_index]
            clip_slot = track.clip_slots[clip_slot_id]
            if not clip_slot.has_clip:
                raise RuntimeError("No clip in slot %d" % clip_slot_id)
            clip = clip_slot.clip
            track.duplicate_clip_to_arrangement(clip, dest_time)
            # Find the new clip at dest_time
            for i, ac in enumerate(track.arrangement_clips):
                if abs(ac.start_time - dest_time) < 0.001:
                    return (track_index, i)
            return (track_index,)

        def split_arrangement_clip(params: Tuple[Any]):
            """Split an arrangement clip at split_time.
            Params: track_id, clip_id, split_time
            Returns: track_id, original_clip_id, new_clip_id
            """
            track_index = int(params[0])
            clip_index = int(params[1])
            split_time = float(params[2])
            track = self.song.tracks[track_index]
            clip = track.arrangement_clips[clip_index]

            orig_start = clip.start_time
            orig_end = clip.end_time

            if split_time <= orig_start or split_time >= orig_end:
                raise ValueError("split_time must be within clip bounds (%f, %f)" % (orig_start, orig_end))

            # Duplicate the clip to the split position
            track.duplicate_clip_to_arrangement(clip, split_time)

            # Re-fetch clips since indices may have changed
            # Find original clip (starts at orig_start) and trim its end
            orig_clip = None
            new_clip = None
            orig_idx = None
            new_idx = None
            for i, ac in enumerate(track.arrangement_clips):
                if abs(ac.start_time - orig_start) < 0.001 and orig_clip is None:
                    orig_clip = ac
                    orig_idx = i
                elif abs(ac.start_time - split_time) < 0.001 and new_clip is None:
                    new_clip = ac
                    new_idx = i

            if orig_clip:
                # Trim original to end at split point
                orig_clip.end_marker = split_time - orig_start
                if orig_clip.looping:
                    orig_clip.loop_end = split_time - orig_start

            if new_clip:
                # Trim new clip's start
                offset = split_time - orig_start
                new_clip.start_marker = offset
                if new_clip.looping:
                    new_clip.loop_start = offset

            return (track_index, orig_idx if orig_idx is not None else clip_index,
                    new_idx if new_idx is not None else -1)

        def move_arrangement_clip(params: Tuple[Any]):
            """Move an arrangement clip to new_start.
            Params: track_id, clip_id, new_start
            Returns: track_id, new_clip_id
            """
            track_index = int(params[0])
            clip_index = int(params[1])
            new_start = float(params[2])
            track = self.song.tracks[track_index]
            clip = track.arrangement_clips[clip_index]

            # Duplicate to new position, then delete original
            track.duplicate_clip_to_arrangement(clip, new_start)
            # Re-fetch the original clip (it's still at the old position)
            old_clip = track.arrangement_clips[clip_index]
            track.delete_clip(old_clip)

            # Find the new clip
            for i, ac in enumerate(track.arrangement_clips):
                if abs(ac.start_time - new_start) < 0.001:
                    return (track_index, i)
            return (track_index, -1)

        def duplicate_arrangement_clip(params: Tuple[Any]):
            """Duplicate an arrangement clip to dest_time.
            Params: track_id, clip_id, dest_time
            Returns: track_id, new_clip_id
            """
            track_index = int(params[0])
            clip_index = int(params[1])
            dest_time = float(params[2])
            track = self.song.tracks[track_index]
            clip = track.arrangement_clips[clip_index]
            track.duplicate_clip_to_arrangement(clip, dest_time)
            for i, ac in enumerate(track.arrangement_clips):
                if abs(ac.start_time - dest_time) < 0.001:
                    return (track_index, i)
            return (track_index, -1)

        self.osc_server.add_handler("/live/track/create_arrangement_clip", create_arrangement_clip)
        self.osc_server.add_handler("/live/track/delete_arrangement_clip", delete_arrangement_clip)
        self.osc_server.add_handler("/live/track/duplicate_to_arrangement", duplicate_to_arrangement)
        self.osc_server.add_handler("/live/track/split_arrangement_clip", split_arrangement_clip)
        self.osc_server.add_handler("/live/track/move_arrangement_clip", move_arrangement_clip)
        self.osc_server.add_handler("/live/track/duplicate_arrangement_clip", duplicate_arrangement_clip)
