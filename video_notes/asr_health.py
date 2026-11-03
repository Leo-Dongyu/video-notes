"""用内存 WAV 检查真实解码接口，不下载模型或调用 API。"""
import io
import wave


def check_audio_decoder():
    from faster_whisper.audio import decode_audio
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b'\x00\x00' * 3200)
    audio.seek(0)
    samples = decode_audio(audio, sampling_rate=16000)
    if len(samples) != 3200:
        raise RuntimeError('Unexpected decoded sample count')
    return len(samples)


if __name__ == '__main__':
    check_audio_decoder()
    print('ASR WAV decode: OK')
