# Fowler Demo Maker as a container (canonical: alicia/demo_maker).
#
# Built on Playwright's image because the app drives headless Chromium in two
# places (server/lib/video.js renders the slide frames, vidyardWidgetUpload.js
# drives the upload widget). Installing Chromium's shared libraries by hand on
# a slim base is the usual way this breaks, so start from the image that
# already has them, and pin to the Playwright version package.json asks for.
FROM mcr.microsoft.com/playwright:v1.52.0-noble

# ffmpeg and ffprobe are called as binaries (execFile('ffmpeg'), not a wrapped
# npm package), so they have to exist on PATH rather than in node_modules.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV NODE_ENV=production

# Dependencies first so a source change does not reinstall them.
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev || npm install --omit=dev

COPY . .

# Everything mutable lives here, not in the image: the SQLite database, the
# render scratch space and the published video library. Mount a volume at this
# path or the work is lost on every restart (see server/lib/paths.js DATA_DIR).
ENV DATA_DIR=/data
RUN mkdir -p /data

ENV PORT=4173
ENV HOST=0.0.0.0
EXPOSE 4173

# The image runs as the non-root 'pwuser' that the Playwright base provides.
RUN chown -R pwuser:pwuser /data /app
USER pwuser

CMD ["node", "server/index.js"]
