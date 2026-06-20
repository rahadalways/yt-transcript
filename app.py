import os
from flask import Flask, request, jsonify
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
)

app = Flask(__name__)

# Simple shared-secret auth so randoms on the internet can't hammer your VPS.
# Set TRANSCRIPT_API_KEY as an env var in Coolify; n8n must send it back as a header.
API_KEY = os.environ.get("TRANSCRIPT_API_KEY", "")


def check_auth():
    if not API_KEY:
        return True  # no key configured -> auth disabled (only do this for quick local testing)
    sent = request.headers.get("X-API-Key", "")
    return sent == API_KEY


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/transcript", methods=["GET"])
def get_transcript():
    if not check_auth():
        return jsonify({"available": False, "error": "unauthorized"}), 401

    video_id = request.args.get("video_id", "").strip()
    if not video_id:
        return jsonify({"available": False, "error": "video_id query param is required"}), 400

    # Preferred language order: Bangla first (in case channel has bn captions),
    # then English, then fall back to whatever is available (incl. auto-generated).
    preferred_langs = ["bn", "en", "en-US", "en-GB"]

    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        transcript = None
        used_lang = None

        # 1) Try manually created transcripts in preferred languages
        try:
            transcript = transcript_list.find_transcript(preferred_langs)
            used_lang = transcript.language_code
        except NoTranscriptFound:
            pass

        # 2) Fall back to auto-generated transcripts in preferred languages
        if transcript is None:
            try:
                transcript = transcript_list.find_generated_transcript(preferred_langs)
                used_lang = transcript.language_code
            except NoTranscriptFound:
                pass

        # 3) Last resort: grab whatever the first available transcript is, then
        #    try to translate it to English if translation is supported.
        if transcript is None:
            available = list(transcript_list)
            if not available:
                raise NoTranscriptFound(video_id, preferred_langs, transcript_list)
            transcript = available[0]
            used_lang = transcript.language_code
            if transcript.is_translatable:
                try:
                    transcript = transcript.translate("en")
                    used_lang = "en (translated)"
                except Exception:
                    pass  # keep original if translation fails

        fetched = transcript.fetch()
        full_text = " ".join(chunk["text"].strip() for chunk in fetched if chunk.get("text"))
        full_text = " ".join(full_text.split())  # collapse whitespace/newlines

        return jsonify({
            "available": True,
            "video_id": video_id,
            "language": used_lang,
            "transcript": full_text,
            "segment_count": len(fetched),
        })

    except TranscriptsDisabled:
        return jsonify({"available": False, "video_id": video_id, "error": "transcripts_disabled"}), 200
    except (NoTranscriptFound, VideoUnavailable) as e:
        return jsonify({"available": False, "video_id": video_id, "error": str(e)}), 200
    except Exception as e:
        # Catch-all so n8n always gets clean JSON instead of a raw 500 HTML page.
        return jsonify({"available": False, "video_id": video_id, "error": f"unexpected_error: {e}"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
