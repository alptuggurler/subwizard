"""SoundDevicePassthrough: capture card audio input -> output device (SPEC 3.2).

Two independent streams joined by a DelayBuffer rather than one duplex stream:
the capture card and the speakers are different devices with different clocks,
and the buffer between them is also what implements `audio.delay_ms`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from subwizard.audio.base import AudioError, BaseAudioPassthrough
from subwizard.audio.devices import wasapi_settings
from subwizard.audio.meter import LevelMeter, db_to_gain
from subwizard.audio.ring import DelayBuffer
from subwizard.core.metrics import M, MetricsHub

log = logging.getLogger(__name__)

MS = 1000.0


@dataclass(frozen=True, slots=True)
class PassthroughSettings:
    input_device: int | None  # PortAudio index; None = system default
    output_device: int | None
    sample_rate: int
    channels: int
    block: int  # frames per callback
    buffer_ms: float  # baseline delay of the latency profile
    delay_ms: float  # extra A/V sync delay on top of the baseline
    max_delay_ms: float
    gain_db: float
    muted: bool


class SoundDevicePassthrough(BaseAudioPassthrough):
    def __init__(
        self, settings: PassthroughSettings, metrics: MetricsHub, meter: LevelMeter
    ) -> None:
        self._s = settings
        self._metrics = metrics
        self._meter = meter
        self._gain = 0.0 if settings.muted else db_to_gain(settings.gain_db)
        self._gain_db = settings.gain_db
        self._muted = settings.muted
        self._delay_ms = max(0.0, settings.delay_ms)
        block_ms = settings.block * MS / settings.sample_rate
        self._buffer = DelayBuffer(
            channels=settings.channels,
            sample_rate=settings.sample_rate,
            target_ms=settings.buffer_ms + self._delay_ms,
            # Fill swings by one block per callback on each side; allow a few
            # blocks of wander before treating it as clock drift.
            slack_ms=max(settings.buffer_ms, 4 * block_ms),
            max_target_ms=settings.buffer_ms + settings.max_delay_ms,
        )
        self._in = None
        self._out = None
        self._stream_latency_ms = 0.0
        self._input_glitches = 0  # PortAudio reported an input overflow
        self._output_glitches = 0  # PortAudio reported an output underflow

    # --- stream callbacks (PortAudio threads) --------------------------------
    def _on_input(self, indata: np.ndarray, frames: int, time: object, status: object) -> None:
        if status:
            self._input_glitches += 1
        self._meter.process(indata)
        self._buffer.write(indata)

    def _on_output(self, outdata: np.ndarray, frames: int, time: object, status: object) -> None:
        if status:
            self._output_glitches += 1
        outdata[:] = self._buffer.read(frames) * self._gain

    # --- BaseAudioPassthrough -------------------------------------------------
    def start(self) -> None:
        import sounddevice as sd  # lazy: importing loads PortAudio

        s = self._s
        common = dict(samplerate=s.sample_rate, channels=s.channels, blocksize=s.block,
                      dtype="float32", latency="low", extra_settings=wasapi_settings())  # fmt: skip
        try:
            self._in = sd.InputStream(device=s.input_device, callback=self._on_input, **common)
            self._out = sd.OutputStream(device=s.output_device, callback=self._on_output, **common)
            self._in.start()
            self._out.start()
        except Exception as exc:  # sd.PortAudioError, ValueError for bad device/params
            log.error(
                "Ses akışı açılamadı (giriş %s, çıkış %s, %d Hz, %d kanal): %s",
                s.input_device, s.output_device, s.sample_rate, s.channels, exc,
            )  # fmt: skip
            self.stop()
            raise AudioError("error.audio_open", detail=str(exc)) from exc

        self._stream_latency_ms = (self._in.latency + self._out.latency) * MS
        self._metrics.set_gauge(M.AUDIO_SAMPLE_RATE, int(self._in.samplerate))
        self._metrics.set_gauge(M.AUDIO_BLOCK, s.block)
        log.info(
            "Ses aktarımı başladı: %d Hz, blok %d, tahmini gecikme %.0f ms",
            int(self._in.samplerate), s.block, self.latency_ms,
        )  # fmt: skip

    def stop(self) -> None:
        for stream in (self._in, self._out):
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception as exc:
                    log.debug("Ses akışı kapatılırken hata: %s", exc)
        self._in = self._out = None

    @property
    def is_running(self) -> bool:
        return self._in is not None and self._out is not None

    @property
    def latency_ms(self) -> float:
        """Estimated capture-to-speaker delay: device latencies + our buffer."""
        return self._stream_latency_ms + self._buffer.target_ms

    def publish_metrics(self) -> None:
        """Called from the UI tick: totals since start, device- and buffer-level combined."""
        m = self._metrics
        m.set_gauge(M.AUDIO_LATENCY_MS, self.latency_ms)
        m.set_gauge(M.AUDIO_UNDERRUN, self._output_glitches + self._buffer.underruns)
        m.set_gauge(M.AUDIO_OVERRUN, self._input_glitches + self._buffer.overruns)

    def set_delay_ms(self, delay_ms: int) -> None:
        self._delay_ms = max(0.0, float(delay_ms))
        self._buffer.set_target_ms(self._s.buffer_ms + self._delay_ms)

    def set_gain_db(self, gain_db: float) -> None:
        self._gain_db = gain_db
        if not self._muted:
            self._gain = db_to_gain(gain_db)

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        self._gain = 0.0 if muted else db_to_gain(self._gain_db)
