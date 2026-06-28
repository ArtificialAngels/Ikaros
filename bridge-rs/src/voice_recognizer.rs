//! Voice Recognizer — sherpa-onnx SenseVoice STT + SileroVAD wrapper.
//!
//! Provides in-process speech-to-text using:
//!   - SenseVoice int8 model (中英日韩粤, ~230MB)
//!   - SileroVAD for voice activity detection
//!
//! All types are Send + Sync — safe for multi-threaded use.

use sherpa_onnx::{
    OfflineRecognizer, OfflineRecognizerConfig,
    VoiceActivityDetector, VadModelConfig,
};
use std::path::Path;
use tracing::{info, warn};

/// In-process voice recognizer using sherpa-onnx.
///
/// Wraps an OfflineRecognizer (SenseVoice) and optional VoiceActivityDetector (SileroVAD).
/// Both are Send + Sync, so this can be shared across threads via Arc.
pub struct VoiceRecognizer {
    recognizer: OfflineRecognizer,
    vad: Option<VoiceActivityDetector>,
}

// Safety: sherpa-onnx types implement Send + Sync
unsafe impl Send for VoiceRecognizer {}
unsafe impl Sync for VoiceRecognizer {}

impl VoiceRecognizer {
    /// Create a new VoiceRecognizer.
    ///
    /// `model_dir` should contain:
    ///   - model.int8.onnx (SenseVoice model)
    ///   - tokens.txt (token vocabulary)
    ///   - silero_vad.onnx (VAD model, optional)
    pub fn new(model_dir: &Path) -> Result<Self, String> {
        let model_path = model_dir.join("model.int8.onnx");
        let tokens_path = model_dir.join("tokens.txt");
        let vad_path = model_dir.join("silero_vad.onnx");

        if !model_path.is_file() {
            return Err(format!("SenseVoice model not found: {}", model_path.display()));
        }
        if !tokens_path.is_file() {
            return Err(format!("tokens.txt not found: {}", tokens_path.display()));
        }

        info!("loading SenseVoice model from {}", model_dir.display());

        // Configure SenseVoice offline recognizer
        let mut config = OfflineRecognizerConfig::default();
        config.model_config.sense_voice = sherpa_onnx::OfflineSenseVoiceModelConfig {
            model: Some(model_path.to_string_lossy().into_owned()),
            language: Some("auto".into()),
            use_itn: true,
        };
        config.model_config.tokens = Some(tokens_path.to_string_lossy().into_owned());
        config.model_config.num_threads = 2;
        config.model_config.provider = Some("cpu".into());
        config.model_config.debug = false;

        let recognizer = OfflineRecognizer::create(&config)
            .ok_or_else(|| "failed to create OfflineRecognizer".to_string())?;

        info!("SenseVoice recognizer created (threads=2, provider=cpu)");

        // Create VAD if model file exists
        let vad = if vad_path.is_file() {
            let mut vad_config = VadModelConfig::default();
            vad_config.silero_vad = sherpa_onnx::SileroVadModelConfig {
                model: Some(vad_path.to_string_lossy().into_owned()),
                threshold: 0.5,
                min_silence_duration: 0.25,
                min_speech_duration: 0.25,
                window_size: 512,
                max_speech_duration: 30.0,
            };
            vad_config.sample_rate = 16000;
            vad_config.num_threads = 1;
            vad_config.provider = Some("cpu".into());
            vad_config.debug = false;

            match VoiceActivityDetector::create(&vad_config, 30.0) {
                Some(v) => {
                    info!("SileroVAD created (window=512, threshold=0.5)");
                    Some(v)
                }
                None => {
                    warn!("failed to create VoiceActivityDetector, VAD disabled");
                    None
                }
            }
        } else {
            warn!("silero_vad.onnx not found at {}, VAD disabled", vad_path.display());
            None
        };

        Ok(Self { recognizer, vad })
    }

    /// Transcribe raw PCM i16 audio (16kHz mono) to text.
    ///
    /// Converts i16 samples to f32, feeds to SenseVoice, returns recognized text.
    /// Returns None if recognition fails or audio is too short.
    pub fn transcribe(&self, pcm_i16: &[i16]) -> Option<String> {
        if pcm_i16.len() < 1600 {
            // Less than 100ms at 16kHz — too short
            return None;
        }

        // Convert i16 PCM to f32 normalized [-1.0, 1.0]
        let samples_f32: Vec<f32> = pcm_i16
            .iter()
            .map(|&s| s as f32 / i16::MAX as f32)
            .collect();

        let stream = self.recognizer.create_stream();
        stream.accept_waveform(16000, &samples_f32);
        self.recognizer.decode(&stream);

        match stream.get_result() {
            Some(result) if !result.text.trim().is_empty() => {
                let text = result.text.trim().to_string();
                info!("STT result ({} chars): {}", text.len(), &text[..text.len().min(80)]);
                Some(text)
            }
            _ => None,
        }
    }

    /// Transcribe with VAD pre-processing.
    ///
    /// Uses SileroVAD to detect speech segments, then runs SenseVoice on each.
    /// Returns all recognized segments (may be empty if no speech detected).
    pub fn transcribe_with_vad(&self, pcm_i16: &[i16]) -> Vec<String> {
        let vad = match &self.vad {
            Some(v) => v,
            None => {
                // No VAD — fall back to direct transcription
                return self.transcribe(pcm_i16)
                    .into_iter()
                    .collect();
            }
        };

        if pcm_i16.len() < 1600 {
            return Vec::new();
        }

        // Convert i16 PCM to f32
        let samples_f32: Vec<f32> = pcm_i16
            .iter()
            .map(|&s| s as f32 / i16::MAX as f32)
            .collect();

        // Feed audio to VAD in 512-sample windows
        let window_size = 512;
        let mut offset = 0;
        while offset + window_size <= samples_f32.len() {
            vad.accept_waveform(&samples_f32[offset..offset + window_size]);
            offset += window_size;
        }
        // Feed remaining samples (pad with zeros if needed)
        if offset < samples_f32.len() {
            let mut last_chunk = samples_f32[offset..].to_vec();
            last_chunk.resize(window_size, 0.0);
            vad.accept_waveform(&last_chunk);
        }

        // Flush any remaining buffered audio
        vad.flush();

        // Collect speech segments and transcribe each
        let mut results = Vec::new();
        while let Some(segment) = vad.front() {
            let speech = segment.samples();
            if speech.len() >= 1600 {
                let stream = self.recognizer.create_stream();
                stream.accept_waveform(16000, speech);
                self.recognizer.decode(&stream);
                if let Some(result) = stream.get_result() {
                    let text = result.text.trim().to_string();
                    if !text.is_empty() {
                        results.push(text);
                    }
                }
            }
            vad.pop();
        }

        // Reset VAD state for next call
        vad.reset();

        if results.is_empty() {
            // VAD found no speech segments — try direct transcription as fallback
            if let Some(text) = self.transcribe(pcm_i16) {
                results.push(text);
            }
        }

        results
    }
}
