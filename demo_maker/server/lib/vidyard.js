const API_URL = 'https://api.vidyard.com/dashboard/v1/players.json';
const widget = require('./vidyardWidgetUpload');

function apiConfigured() {
  return Boolean(process.env.VIDYARD_API_TOKEN);
}

function configured() {
  // Prefer the uploader-widget path (no Dashboard API needed). Token remains a
  // legacy fallback for accounts that actually have API access.
  return widget.configured() || apiConfigured();
}

async function createPlayer({ name, uploadUrl, description = '' }) {
  if (!apiConfigured()) throw new Error('VIDYARD_API_TOKEN is not configured');

  const response = await fetch(API_URL, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      auth_token: process.env.VIDYARD_API_TOKEN,
      player: {
        name,
        chapters_attributes: [{
          position: 0,
          video_attributes: {
            name,
            upload_url: uploadUrl,
            ...(description ? { description } : {}),
          },
        }],
      },
    }),
  });

  const text = await response.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = {};
  }
  if (!response.ok) {
    const detail = data.message || data.error || text || response.statusText;
    throw new Error(`Vidyard returned ${response.status}: ${detail}`);
  }

  const player = data.player || data;
  if (!player.uuid) throw new Error('Vidyard created the player but did not return its UUID');
  return {
    id: player.id,
    uuid: player.uuid,
    shareUrl: `https://share.vidyard.com/watch/${player.uuid}`,
  };
}

/**
 * Put a local mp4 into Vidyard. Uses the headless uploader widget when no API
 * token exists (the Clearspeed package); falls back to Dashboard URL-import.
 */
async function uploadLocalFile({ filePath, name, description = '', publicUploadUrl = '' } = {}) {
  if (widget.configured()) {
    const result = await widget.uploadFile({ filePath, name });
    return { uuid: result.uuid, shareUrl: result.shareUrl, via: result.via };
  }
  if (!apiConfigured()) throw new Error('No Vidyard upload path configured');
  if (!publicUploadUrl) throw new Error('publicUploadUrl required for Dashboard API import');
  const player = await createPlayer({ name, uploadUrl: publicUploadUrl, description });
  return { ...player, via: 'api' };
}

module.exports = { configured, apiConfigured, createPlayer, uploadLocalFile };
