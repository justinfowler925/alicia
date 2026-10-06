/**
 * The demo player's own language.
 *
 * A translated demo is not just translated questions: the Clearspeed UI the
 * player recreates would itself be in the participant's language, and the
 * language selector in the top-right of the Questions panel would show that
 * language's flag. Rendering Arabic questions under "🇺🇸 English ▾" is the tell
 * that gives a demo away.
 *
 * This module resolves a demo's locale (code, direction, flag, label) and holds
 * the English chrome strings — the single source of truth for which strings are
 * translatable. `translateUiStrings()` (lib/translate.js) turns this pack into
 * the target language; the result is stored on the project and embedded in the
 * player config, so a render never depends on a live translation call.
 */

// Every string the player paints that is chrome rather than data. Org names,
// region names, IRNs and the question/answer text are data and stay as they are.
const UI_EN = {
  // Questions panel
  crumbQuestions: 'Questions',
  region: 'Region',
  searchQuestion: 'Search by Question',
  showOnlyPertinent: 'Show Only Pertinent',
  newBtn: 'New',
  thNo: 'NO',
  thQuestion: 'QUESTION',
  thType: 'TYPE',
  thAnswer: 'ANSWER',
  thAudio: 'AUDIO',
  pertinent: 'Pertinent',
  neutral: 'Neutral',
  rowsPerPage: 'Rows per page',
  ofCount: 'of',

  // Participants panel
  crumbParticipants: 'Participants',
  testMode: 'Test Mode',
  searchIrn: 'Search by IRN',
  filter: 'Filter',
  download: 'Download',
  action: 'Action',
  thIrn: 'IRN',
  thName: 'NAME',
  thCreatedOn: 'CREATED ON',
  thStatus: 'STATUS',
  thUpdatedOn: 'UPDATED ON',
  thSummary: 'SUM…',
  statusInProgress: 'In Progress',
  statusCompleted: 'Completed',
  statusIncomplete: 'Incomplete',
  statusCreated: 'Created',

  // Voice-analysis workflow
  wfTitle: 'VOICE SIGNAL ANALYSIS',
  wfCaptured: 'CAPTURED RESPONSE',
  stepRecord: 'Record',
  stepAnalyze: 'Analyze',
  stepScore: 'Score',
  stIdle: 'Idle',
  stRecording: 'Recording',
  stAnalyzing: 'Analyzing',
  stScoring: 'Scoring',
  stCaptured: 'Captured',
  stHighRisk: 'High risk',
  stClear: 'Clear',
  stIteration: 'Iteration {n}',
  stNotScored: 'Not scored',

  // The verdict chip. English shows these upper-case; a language whose script
  // has no case keeps its ordinary form.
  resPending: 'PENDING',
  resAnalyzing: 'ANALYZING',
  resScoring: 'SCORING',
  resClear: 'CLEAR',
  resHighRisk: 'HIGH RISK',
  resIteration: 'ITERATION {n}',
  resNotScored: 'NOT SCORED',

  // Live feed line under the waveform
  feedAwaiting: 'awaiting participant',
  feedRecording: 'recording response…',
  feedCaptured: 'response captured',
  feedNotScoredIter: 'iteration {n} — not scored',
  feedNotScored: 'not scored',
  feedTranscript: 'transcript:',
  feedAnalyzing: 'analyzing voice signal',
  feedScoring: 'computing risk score',
  feedElevated: 'signal elevated — flagged for review',
  feedExpected: 'signal within expected range',

  // Sub-heading on a production demo: "ITERATION 2 · PERTINENT · SCORED"
  subIteration: 'ITERATION {n}',
  subNeutral: 'NEUTRAL',
  subPertinent: 'PERTINENT',
  subScored: 'SCORED',

  // Play overlay (screen only — never in a rendered frame)
  playDemo: 'Play {org} demo',
};

const UI_KEYS = Object.keys(UI_EN);

// Right-to-left scripts. The player mirrors its layout for these.
const RTL_LANGS = new Set(['ar', 'he', 'fa', 'ur', 'ps', 'sd', 'ug', 'yi', 'dv', 'ckb']);

// Where a language-only tag ("de", "ar") plants its flag. Voices always carry a
// region ("ar-AE", "en-GB"), so this only backs up a hand-set override.
const DEFAULT_REGION = {
  en: 'US', ar: 'AE', de: 'DE', es: 'ES', fr: 'FR', pt: 'BR', it: 'IT', nl: 'NL',
  pl: 'PL', cs: 'CZ', sk: 'SK', ro: 'RO', hu: 'HU', el: 'GR', bg: 'BG', hr: 'HR',
  sr: 'RS', uk: 'UA', ru: 'RU', tr: 'TR', he: 'IL', fa: 'IR', ur: 'PK', hi: 'IN',
  ta: 'IN', te: 'IN', bn: 'BD', ja: 'JP', ko: 'KR', zh: 'CN', th: 'TH', vi: 'VN',
  id: 'ID', ms: 'MY', tl: 'PH', fil: 'PH', sw: 'KE', af: 'ZA', da: 'DK', sv: 'SE',
  nb: 'NO', no: 'NO', fi: 'FI', is: 'IS', et: 'EE', lv: 'LV', lt: 'LT', sl: 'SI',
  ca: 'ES', eu: 'ES', gl: 'ES', ga: 'IE', cy: 'GB', sq: 'AL', mk: 'MK', hy: 'AM',
  ka: 'GE', az: 'AZ', kk: 'KZ', uz: 'UZ', mn: 'MN', ne: 'NP', si: 'LK', km: 'KH',
  lo: 'LA', my: 'MM', am: 'ET', so: 'SO', ha: 'NG', yo: 'NG', ig: 'NG', zu: 'ZA',
};

// Country names as they are typed into a project's Region field → flag. The
// locale's own flag is the fallback, which is right far more often than 🇺🇸 was.
const REGION_FLAG = {
  'united states': 'US', 'usa': 'US', 'us': 'US',
  'united kingdom': 'GB', 'uk': 'GB', 'great britain': 'GB', 'england': 'GB',
  'scotland': 'GB', 'wales': 'GB', 'northern ireland': 'GB',
  'united arab emirates': 'AE', 'uae': 'AE', 'u.a.e': 'AE', 'u.a.e.': 'AE',
  'dubai': 'AE', 'abu dhabi': 'AE',
  'saudi arabia': 'SA', 'ksa': 'SA', 'qatar': 'QA', 'kuwait': 'KW',
  'bahrain': 'BH', 'oman': 'OM', 'jordan': 'JO', 'egypt': 'EG', 'israel': 'IL',
  'lebanon': 'LB', 'iraq': 'IQ', 'morocco': 'MA', 'tunisia': 'TN', 'algeria': 'DZ',
  'canada': 'CA', 'mexico': 'MX', 'brazil': 'BR', 'argentina': 'AR', 'chile': 'CL',
  'colombia': 'CO', 'peru': 'PE', 'panama': 'PA', 'costa rica': 'CR',
  'australia': 'AU', 'new zealand': 'NZ', 'ireland': 'IE', 'france': 'FR',
  'germany': 'DE', 'spain': 'ES', 'portugal': 'PT', 'italy': 'IT',
  'netherlands': 'NL', 'belgium': 'BE', 'switzerland': 'CH', 'austria': 'AT',
  'sweden': 'SE', 'norway': 'NO', 'denmark': 'DK', 'finland': 'FI', 'iceland': 'IS',
  'poland': 'PL', 'czechia': 'CZ', 'czech republic': 'CZ', 'slovakia': 'SK',
  'hungary': 'HU', 'romania': 'RO', 'bulgaria': 'BG', 'greece': 'GR',
  'turkey': 'TR', 'ukraine': 'UA', 'georgia': 'GE', 'kazakhstan': 'KZ',
  'india': 'IN', 'pakistan': 'PK', 'bangladesh': 'BD', 'sri lanka': 'LK',
  'japan': 'JP', 'south korea': 'KR', 'korea': 'KR', 'china': 'CN',
  'hong kong': 'HK', 'taiwan': 'TW', 'singapore': 'SG', 'malaysia': 'MY',
  'indonesia': 'ID', 'thailand': 'TH', 'vietnam': 'VN', 'philippines': 'PH',
  'south africa': 'ZA', 'nigeria': 'NG', 'kenya': 'KE', 'ghana': 'GH',
  'ethiopia': 'ET', 'tanzania': 'TZ', 'uganda': 'UG',
};

// The inverse of REGION_FLAG for the codes a demo locale can actually produce:
// the canonical name to print in the player's region selector. Only the codes
// DEFAULT_REGION and the voice tags can yield need an entry; anything missing
// falls back to the stored value.
const REGION_NAME = {
  US: 'United States', GB: 'United Kingdom', AE: 'United Arab Emirates',
  SA: 'Saudi Arabia', QA: 'Qatar', KW: 'Kuwait', BH: 'Bahrain', OM: 'Oman',
  JO: 'Jordan', EG: 'Egypt', IL: 'Israel', LB: 'Lebanon', IQ: 'Iraq',
  MA: 'Morocco', TN: 'Tunisia', DZ: 'Algeria', CA: 'Canada', MX: 'Mexico',
  BR: 'Brazil', AR: 'Argentina', CL: 'Chile', CO: 'Colombia', PE: 'Peru',
  AU: 'Australia', NZ: 'New Zealand', IE: 'Ireland', FR: 'France',
  DE: 'Germany', ES: 'Spain', PT: 'Portugal', IT: 'Italy', NL: 'Netherlands',
  BE: 'Belgium', CH: 'Switzerland', AT: 'Austria', SE: 'Sweden', NO: 'Norway',
  DK: 'Denmark', FI: 'Finland', IS: 'Iceland', PL: 'Poland', CZ: 'Czechia',
  SK: 'Slovakia', HU: 'Hungary', RO: 'Romania', BG: 'Bulgaria', GR: 'Greece',
  TR: 'Turkey', UA: 'Ukraine', GE: 'Georgia', KZ: 'Kazakhstan', IN: 'India',
  PK: 'Pakistan', BD: 'Bangladesh', LK: 'Sri Lanka', JP: 'Japan',
  KR: 'South Korea', CN: 'China', HK: 'Hong Kong', TW: 'Taiwan',
  SG: 'Singapore', MY: 'Malaysia', ID: 'Indonesia', TH: 'Thailand',
  VN: 'Vietnam', PH: 'Philippines', ZA: 'South Africa', NG: 'Nigeria',
  KE: 'Kenya', GH: 'Ghana', ET: 'Ethiopia', TZ: 'Tanzania', UG: 'Uganda',
};

// `projects.demo_region` carries a column DEFAULT of 'United States', so every
// project holds it whether anyone chose it or not — which is exactly why nine
// live demos rendered an Arabic or British interface next to "Region 🇺🇸 United
// States". It is therefore treated as UNSET, and the region is derived from the
// demo's own locale instead. Any other stored value is a deliberate override
// and always wins (project 32's hand-typed "U.A.E" is the reason this matters).
const LEGACY_REGION_DEFAULT = 'united states';

/**
 * The region name the player's selector should show.
 *
 * @param {string} storedRegion  projects.demo_region
 * @param {{region: string}} locale  from resolveDemoLocale
 */
function resolveRegionName(storedRegion, locale) {
  const stored = String(storedRegion || '').trim();
  if (stored && stored.toLowerCase() !== LEGACY_REGION_DEFAULT) return stored;
  return REGION_NAME[String(locale && locale.region || '').toUpperCase()] || stored || 'United States';
}

/** Regional-indicator flag emoji for a 2-letter country code. */
function flagFor(region) {
  const cc = String(region || '').trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(cc)) return '';
  return String.fromCodePoint(...[...cc].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65));
}

/**
 * The language's own name for itself ("Deutsch", "العربية"), via ICU.
 *
 * ICU returns these the way the language writes them in running text, which is
 * lower-case for several ("español", "čeština"); a selector label capitalizes.
 * Locale-aware upper-casing, so it stays a no-op for scripts without case.
 */
function endonym(lang, code) {
  for (const [inLocale, of] of [[code, lang], ['en', lang]]) {
    try {
      const name = new Intl.DisplayNames([inLocale], { type: 'language' }).of(of);
      if (name && name !== of) return name.charAt(0).toLocaleUpperCase(inLocale) + name.slice(1);
    } catch { /* no ICU data for this tag */ }
  }
  return String(code || '').toUpperCase();
}

/**
 * Parse a voice's language tag ("ar-AE", "en-GB", "de") into the locale the
 * player needs. Unknown/blank tags fall back to US English, which is what every
 * demo rendered before this existed.
 */
function parseLocaleTag(tag) {
  const raw = String(tag || '').trim().replace(/_/g, '-');
  const m = raw.match(/^([A-Za-z]{2,3})(?:-[A-Za-z]{4})?(?:-([A-Za-z]{2}))?/);
  const lang = m ? m[1].toLowerCase() : 'en';
  const region = (m && m[2] ? m[2] : DEFAULT_REGION[lang] || '').toUpperCase();
  const code = region ? `${lang}-${region}` : lang;
  return {
    code,
    lang,
    region,
    dir: RTL_LANGS.has(lang) ? 'rtl' : 'ltr',
    // A globe rather than nothing when the tag carries no usable region — the
    // selector must never render as a bare label with a gap where a flag was.
    langFlag: flagFor(region) || '🌐',
    langLabel: endonym(lang, code),
  };
}

/** English is the player's built-in language — no translated pack needed. */
function isEnglish(locale) {
  return !locale || locale.lang === 'en';
}

/** How to name this locale to the translator, e.g. "Arabic (United Arab Emirates)". */
function englishName(locale) {
  try {
    const name = new Intl.DisplayNames(['en'], { type: 'language' }).of(locale.code);
    if (name && name !== locale.code) return name;
  } catch { /* no ICU data */ }
  return locale.langLabel || locale.code;
}

/** Whether a stored chrome pack is usable for this locale. */
function hasPackFor(stored, code) {
  if (!stored) return false;
  try {
    const pack = typeof stored === 'string' ? JSON.parse(stored) : stored;
    return Boolean(pack && pack.code === code && pack.t && Object.keys(pack.t).length);
  } catch {
    return false;
  }
}

// Script detection, to sanity-check an inferred language against the text that
// is actually on screen. Ranges are deliberately coarse — this only has to tell
// "these are Latin letters" from "these are Arabic letters".
const SCRIPT_RANGES = [
  ['latin', /[A-Za-zÀ-ɏ]/g],
  ['arabic', /[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]/g],
  ['hebrew', /[֐-׿]/g],
  ['cyrillic', /[Ѐ-ӿ]/g],
  ['greek', /[Ͱ-Ͽ]/g],
  ['devanagari', /[ऀ-ॿ]/g],
  ['bengali', /[ঀ-৿]/g],
  ['tamil', /[஀-௿]/g],
  ['thai', /[฀-๿]/g],
  ['han', /[㐀-䶿一-鿿]/g],
  ['kana', /[぀-ヿ]/g],
  ['hangul', /[ᄀ-ᇿ㄰-㆏가-힯]/g],
  ['armenian', /[԰-֏]/g],
  ['georgian', /[Ⴀ-ჿ]/g],
];

// Which script(s) a language is written in. Anything unlisted is Latin.
const LANG_SCRIPTS = {
  ar: ['arabic'], fa: ['arabic'], ur: ['arabic'], ps: ['arabic'], sd: ['arabic'],
  ug: ['arabic'], ckb: ['arabic'], he: ['hebrew'], yi: ['hebrew'],
  ru: ['cyrillic'], uk: ['cyrillic'], be: ['cyrillic'], bg: ['cyrillic'],
  sr: ['cyrillic', 'latin'], mk: ['cyrillic'], kk: ['cyrillic'], mn: ['cyrillic'],
  el: ['greek'], hi: ['devanagari'], mr: ['devanagari'], ne: ['devanagari'],
  sa: ['devanagari'], bn: ['bengali'], ta: ['tamil'], th: ['thai'],
  zh: ['han'], ja: ['kana', 'han'], ko: ['hangul', 'han'],
  hy: ['armenian'], ka: ['georgian'],
};

/** The script most of this text is written in, or '' when there isn't one. */
function dominantScript(text) {
  let best = '';
  let bestN = 0;
  for (const [name, re] of SCRIPT_RANGES) {
    const n = (text.match(re) || []).length;
    if (n > bestN) { best = name; bestN = n; }
  }
  return bestN >= 8 ? best : '';
}

/**
 * Which language a demo is in.
 *
 * `project.demo_language` wins when set (the Demo video settings picker, and
 * what /translate stamps on the project it creates). Otherwise it is the
 * language of the voices actually speaking — which is exactly what /translate
 * re-voices, so a translated project resolves correctly with nothing set.
 *
 * The voices are only believed when the text agrees with them. A project's
 * voices can be swapped long after its text was written — `Embassy Clearance`
 * on Atlas5 is an English demo cast with ar-SA voices — and flipping a published
 * English demo to an Arabic interface on its next re-render is far worse than
 * asking for one dropdown. A positive script mismatch falls back to English; an
 * indecisive one (short text, digits) trusts the voices. Languages that share
 * the Latin script can't be told apart this way, and don't need to be: the
 * chrome stays legible either way, and the picker is the override.
 *
 * ElevenLabs voices all report en-US regardless of what they are saying, so a
 * non-English ElevenLabs demo is the other case that needs the picker.
 */
function resolveDemoLocale(project, segments) {
  const override = (project && project.demo_language || '').trim();
  if (override) return parseLocaleTag(override);

  const counts = new Map();
  const spoken = [];
  for (const s of segments || []) {
    if (!s || !s.text || !String(s.text).trim()) continue;
    spoken.push(String(s.text));
    const tag = (s.language_code || '').trim();
    if (!tag) continue;
    counts.set(tag, (counts.get(tag) || 0) + 1);
  }
  let best = '';
  let bestN = 0;
  for (const [tag, n] of counts) if (n > bestN) { best = tag; bestN = n; }

  const locale = parseLocaleTag(best || 'en-US');
  const script = dominantScript(spoken.join(' '));
  const expected = LANG_SCRIPTS[locale.lang] || ['latin'];
  if (script && !expected.includes(script)) return parseLocaleTag('en-US');
  return locale;
}

/**
 * The `ui` block the player reads. Missing/foreign stored strings degrade to
 * English chrome rather than failing a render — the flag and language label are
 * still correct, which is the part that gives a demo away.
 */
function buildUiConfig(locale, storedStrings, storedRegionName) {
  const regionName = resolveRegionName(storedRegionName, locale);
  let t = null;
  if (storedStrings) {
    try {
      const pack = typeof storedStrings === 'string' ? JSON.parse(storedStrings) : storedStrings;
      // A pack from a different language is stale — ignore it rather than paint it.
      if (pack && pack.code === locale.code && pack.t) t = pack.t;
    } catch { /* unparseable pack — fall back to English */ }
  }
  const strings = { ...UI_EN };
  if (t) for (const k of UI_KEYS) if (typeof t[k] === 'string' && t[k].trim()) strings[k] = t[k];

  return {
    code: locale.code,
    dir: locale.dir,
    langLabel: locale.langLabel,
    langFlag: locale.langFlag,
    // Both travel in the ui block so deriveConfig prints the same region the
    // flag was computed from — a name and a flag resolved independently is how
    // "United States 🇦🇪" would happen.
    regionName,
    regionFlag: regionFlagFor(regionName, locale),
    translated: Boolean(t),
    t: strings,
  };
}

function regionFlagFor(regionName, locale) {
  const key = String(regionName || '').trim().toLowerCase();
  const cc = REGION_FLAG[key];
  return flagFor(cc) || locale.langFlag || '';
}

module.exports = {
  UI_EN,
  UI_KEYS,
  flagFor,
  parseLocaleTag,
  resolveDemoLocale,
  buildUiConfig,
  regionFlagFor,
  resolveRegionName,
  REGION_NAME,
  isEnglish,
  englishName,
  hasPackFor,
};
