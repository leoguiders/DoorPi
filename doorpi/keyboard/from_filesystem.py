"""The file-based pseudo keyboard

This keyboard module simulates input/output events by using files. This
may be useful for triggering events remotely via SSH, integrating with
other software suites, or for testing purposes.

Requirements
************

- The `watchdog` python module
- Write access to an arbitrary directory

  > **Note**: Using files on a persistent filesystem is not recommended,
  > as it actively degrades the life time of flash media like SD cards.
  > Consider using paths on tmpfs filesystems, like `/run` or `/tmp`.

Usage
*****

1. Create a keyboard of type "filesystem"
2. In its settings section, set `base_path_input` and
   `base_path_output` to the directory paths where the input/output
   files should be created. The two directories should be different.
   The default values are `/run/doorpi/<keyboard_name>/in` for input
   and `/run/doorpi/<keyboard_name>/out` for output files. The
   directories configured here will be created if they do not already
   exist, as long as DoorPi has the permissions necessary to do so.

   If `reset_input` is set to True (default), the input files will be
   reset to (logical) false after being read. Whether that corresponds
   to the value True or False depends on the value of `polarity`. Set
   `reset_input` to False to disable this behavior.
3. Define the keyboard's input/output pins and their actions as normal.
   The names of the pins will be used as file names. You do not need
   to specify aliases for output pins, as they default to the pin name.

After starting, the keyboard module will create the directories and
files according to its configuration. Each file will initially contain
the word "False" (or "True" if the keyboard's polarity is LOW). DoorPi
will then start watching for changes to the input files and write out
output pin states to the output files.

DoorPi itself will only write out "True" or "False" for the pin states
"high" and "low" respectively, but it recognizes a few more values in
input files. For a full list of all possible "high" values, see the
"HIGH_LEVEL" list in `doorpi/keyboard/__init__.py`. Everything that is
not "high" will be recognized as "low", except for empty files which
will retain their previous logical pin state.

Events will only be triggered if the logical pin state changes. If a
"high" value is written to a file that contains a different "high"
value, no event will be triggered. The same applies for "low" values.
"""

import logging
import watchdog.events
import watchdog.observers

from pathlib import Path
from time import sleep

import doorpi

from . import SECTION_TPL
from .abc import AbstractKeyboard

logger = logging.getLogger(__name__)


def instantiate(name): return FilesystemKeyboard(name)


class FilesystemKeyboard(AbstractKeyboard, watchdog.events.FileSystemEventHandler):

    def __init__(self, name):
        super().__init__(name)

        conf = doorpi.DoorPi().config
        section_name = SECTION_TPL.format(name=name)
        self.__reset_input = conf.get_bool(section_name, "reset_input", True)
        self.__base_path_input = Path(conf.get_string_parsed(
            section_name, "base_path_input", f"/run/doorpi/{name}/in"))
        self.__base_path_output = Path(conf.get_string_parsed(
            section_name, "base_path_output", f"/run/doorpi/{name}/out"))
        self.__input_states = dict.fromkeys(self._inputs, False)

        if not self.__base_path_input:
            raise ValueError(f"{self.name}: base_path_input must not be empty")
        if not self.__base_path_output:
            raise ValueError(f"{self.name}: base_path_output must not be empty")

        self.__base_path_input.mkdir(parents=True, exist_ok=True)
        self.__base_path_output.mkdir(parents=True, exist_ok=True)

        for pin in self._inputs:
            f = self.__base_path_input / pin
            self.__write_file(f)
            f.chmod(0o666)

        for pin in self._outputs:
            f = self.__base_path_output / pin
            self.__write_file(f)
            f.chmod(0o644)

        self.__observer = watchdog.observers.Observer()
        self.__observer.schedule(self, str(self.__base_path_input))
        self.__observer.start()

    def __del__(self):
        self._deactivate()
        doorpi.DoorPi().event_handler.unregister_source(self._event_source, force=True)

        for pin in self._inputs:
            try: (self.__base_path_input / pin).unlink()
            except FileNotFoundError: pass
            except Exception: logger.exception("%s: Unable to unlink virtual input pin %s",
                                               self.name, pin)
        for pin in self._outputs:
            try: (self.__base_path_output / pin).unlink()
            except FileNotFoundError: pass
            except Exception: logger.exception("%s: Unable to unlink virtual output pin %s",
                                               self.name, pin)

        for d in (self.__base_path_input, self.__base_path_output):
            try: d.rmdir()
            except Exception as ex: logger.error("%s: Cannot remove directory %s: %s",
                                                 self.name, d, ex)
        super().__del__()

    def _deactivate(self):
        self.__observer.stop()
        self.__observer.join()
        super()._deactivate()

    def input(self, pin):
        if pin not in self._inputs: return False
        val = self.__read_file(self.__base_path_input / pin)
        if val is None:
            val = self.__input_states[pin]
            logger.debug("%s: File %s is empty, providing last known value (%s)",
                         self.name, pin, val)
        else:
            logger.debug("%s: Read %s from %s", self.name, val, pin)
            self.__input_states[pin] = val
        return val

    def output(self, pin, value):
        if pin not in self._outputs: return False
        logger.debug("%s: Setting pin %s to %s", self.name, pin, value)
        if self.__write_file(self.__base_path_output / pin, value):
            self._outputs[pin] = value
            return True
        else:
            return False

    def __read_file(self, pin):
        try:
            val = pin.read_text().strip().split()[0]
        except OSError:
            logger.exception("%s: Error reading pin %s", self.name, pin.name)
            return None
        if not val.strip(): return None
        else: return self._normalize(val)

    def __write_file(self, pin, value=False):
        value = self._normalize(value)
        try:
            pin.write_text("1" if value else "0")
        except OSError:
            logger.exception("%s: Error setting pin %s to %s", self.name, pin.name, value)
            return False
        return True

    def on_modified(self, event):
        "Called by the watchdog library when an inotify event was triggered"

        if not isinstance(event, watchdog.events.FileModifiedEvent): return

        pin = Path(event.src_path)
        if pin.name not in self._inputs or pin.parent != self.__base_path_input:
            logger.warning("%s: Received unsolicited FileModifiedEvent for %s",
                           self.name, event.src_path)
            return

        val = self.__read_file(pin)

        if val is None:
            logger.debug("%s: Skipping FileModifiedEvent for %s, file is empty",
                         self.name, pin)
            return

        if val == self.__input_states[pin.name]:
            logger.debug("%s: Skipping FileModifiedEvent for %s, logical value unchanged (%s)",
                         self.name, pin, val)
            return

        self.__input_states[pin] = val
        if val:
            logger.info("%s: Pin %s flanked to logical TRUE, firing OnKeyDown",
                        self.name, pin.name)
            self._fire_OnKeyDown(pin.name)
            if self.__reset_input:
                self.__write_file(pin, False)
        else:
            logger.info("%s: Pin %s flanked to logical FALSE, firing OnKeyUp",
                        self.name, pin)
            self._fire_OnKeyUp(pin.name)
