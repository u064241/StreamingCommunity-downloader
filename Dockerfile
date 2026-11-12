FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Runs as an unprivileged user (uid 10001) unless the compose file says `user:`
# (#28). Everything above stays root-owned, so the panel cannot rewrite its own
# code. The places it writes inside the image belong to that user and are still
# open to any uid, for a compose `user:` override: tmp/ (TMP_DIR, HLS segments
# while a job runs), config/ (database, settings and schedule, normally a
# volume that hides this one) and videos/. Sticky like /tmp, so one uid cannot
# delete another's files. Same paths as before, so a volume already mounted on
# any of them keeps working, provided the host folder is writable by the uid
# the container runs as.
RUN groupadd --gid 10001 scpanel \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app/tmp --shell /usr/sbin/nologin scpanel \
    && mkdir -p /app/tmp /app/config /app/videos \
    && chown 10001:10001 /app/tmp /app/config /app/videos \
    && chmod 1777 /app/tmp /app/config /app/videos

# The working directory is read-only for that user: state lives in config/ and
# downloads in videos/ unless the deployment says otherwise.
ENV DB_FILE=/app/config/panel.db \
    DATA_FILE=/app/config/data.json \
    SCHEDULE_FILE=/app/config/schedule.json \
    VIDEOS_DIR=/app/videos

USER 10001:10001

EXPOSE 8000

# /login is public and always answers (it redirects to / when there is no
# login to do). No curl in the slim image, hence Python.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/login', timeout=4)"]

# How this panel gets updated, which the new-version dialog has to explain.
ENV SCPANEL_INSTALL=docker

CMD ["python", "main.py"]
