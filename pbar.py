import logging
import shutil
import sys
import time
import weakref
from typing import Optional, Callable, Iterable, Iterator, TypeVar

T = TypeVar("T")


class ProgressBar:
    _instances = weakref.WeakSet()

    # Default Unicode box character constants
    DEFAULT_FILLED = '■'  # U+25A0
    DEFAULT_EMPTY = '□'   # U+25A1

    def __init__(
        self,
        name: str,
        total: Optional[int] = None,
        iterable: Optional[Iterable[T]] = None,
        width: Optional[int] = None,
        interval: float = 60.0,
        filled_char: str = DEFAULT_FILLED,
        empty_char: str = DEFAULT_EMPTY,
        output_func: Callable[[str], None] = None,
    ):
        """Initialize progress bar with customizable options.

        Args:
            name: Name of the progress bar for display.
            total: Total number of steps (optional if iterable is provided).
            iterable: Iterable to track progress (optional).
            width: Width of the bar in characters (None to auto-detect terminal width).
            interval: Minimum interval between updates in seconds.
            filled_char: Character for filled portion of the bar.
            empty_char: Character for empty portion of the bar.
            output_func: Function to handle output (defaults to logging.info).
        """
        if iterable is not None and total is None:
            try:
                total = len(iterable)
            except TypeError:
                total = None  # Iterable with unknown length
        elif total is None:
            raise ValueError("Either total or iterable must be provided")

        self.name = name
        self.total = max(1, total) if total is not None else None
        self.iterable = iterable
        self.width = width if width is not None else self._get_terminal_width()
        self.interval = interval
        self.filled_char = filled_char
        self.empty_char = empty_char
        self.output_func = output_func or (lambda msg: logging.info(msg))
        self.start_time = time.monotonic()
        self.last_update = self.start_time
        self.current_progress = 0

        # Register the instance
        type(self)._instances.add(self)

    def _get_terminal_width(self) -> int:
        """Get terminal width, falling back to 80 if undetectable."""
        try:
            return min(shutil.get_terminal_size().columns - 20, 80)  # Reserve space for text
        except Exception:
            return 60  # Safe default

    def get_bar(self, progress: int) -> str:
        """Return progress bar string for given progress."""
        progress = min(max(0, progress), self.total) if self.total is not None else progress
        if self.total is None:
            # For unknown total, show progress without percentage or bar
            elapsed = time.monotonic() - self.start_time
            return f'{self.name}: {progress} items processed [Elapsed: {elapsed:.1f}s]'
        filled = int(self.width * progress // self.total)
        empty = self.width - filled
        bar = self.filled_char * filled + self.empty_char * empty
        percent = (progress / self.total) * 100
        elapsed = time.monotonic() - self.start_time
        eta = (elapsed / max(progress, 1)) * (self.total - progress) if progress > 0 else 0
        return (
            f'{self.name}: |{bar}| {progress}/{self.total} ({percent:.1f}%) '
            f'[ETA: {eta:.1f}s]'
        )

    def update(self, progress: int) -> None:
        """Update the internal progress state and possibly display the progress bar.

        Args:
            progress: Current progress value.
        """
        self.current_progress = progress
        self.maybe_display()

    def maybe_display(self) -> None:
        """Display the progress bar if the time interval has elapsed."""
        current_time = time.monotonic()
        if current_time - self.last_update >= self.interval:
            self.output_func(self.get_bar(self.current_progress))
            self.last_update = current_time

    @classmethod
    def display_bars(cls) -> None:
        """Call maybe_display on all live ProgressBar instances."""
        for inst in list(cls._instances):
            inst.maybe_display()

    def __iter__(self) -> Iterator[T]:
        """Iterate over the provided iterable, updating progress."""
        if self.iterable is None:
            raise ValueError("No iterable provided for iteration")
        for i, item in enumerate(self.iterable):
            self.update(i + 1)
            yield item
            # Ensure final update after last item if total is known
            if self.total is not None and i + 1 == self.total:
                self.update(i + 1)
