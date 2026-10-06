/**
 * Demo format templates.
 *
 * A template stamps out a project's full segment list for a known assessment
 * pattern, so a new demo is "pick the format, pick the question count, fill in
 * your question text" instead of hand-building 40+ segments and hand-tagging
 * every one of them.
 *
 * Two formats:
 *
 *   sales      — Intro, then N × (pertinent question, answer), then Closing.
 *                Every question is scored. This is the pattern every demo in
 *                the library used before templates existed.
 *
 *   production — mirrors the real assessment: the same question set is asked
 *                twice, and only the SECOND iteration's pertinent questions
 *                are scored. Neutral questions are never scored; they exist to
 *                establish the voice baseline. Order for P pertinent questions
 *                is two neutrals up front, then each pertinent question
 *                followed by a neutral:
 *
 *                  P=4 → NQ1 NQ2 PQ1 NQ3 PQ2 NQ4 PQ3 NQ5 PQ4 NQ6
 *                  P=3 → NQ1 NQ2 PQ1 NQ3 PQ2 NQ4 PQ3 NQ5
 *
 *                so a P-question production demo has P pertinent and P+2
 *                neutral questions, asked twice, with a transition segment in
 *                between and P scored columns on the results panel.
 */

const INTRO_TEXT =
  'You will now be asked some simple questions which only require accurate yes or no responses.\n'
  + 'Before answering yes or no, please wait for the question to be completely finished.\n'
  + 'The questions are about to begin\n';

const TRANSITION_TEXT =
  'You will now be asked the same questions a second time.  The questions are about to begin.';

const CLOSING_TEXT =
  'Please hold while the system processes your answers. Your responses were accepted for evaluation.  Thank you for your cooperation.';

// The standard neutral set, taken verbatim from the Embassy Walk-Ins production
// demo. Neutrals establish the voice baseline, so they are deliberately
// innocuous and verifiably answerable — and each carries its OWN answer, because
// they are not uniformly "Yes": NQ1 is a no-question by design.
const NEUTRAL_QUESTIONS = [
  { text: 'Is this the year 1995?', answer: 'No' },
  { text: 'Have you ever watched television?', answer: 'Yes' },
  { text: 'Have you ever seen a car?', answer: 'Yes' },
  { text: 'Have you ever seen a truck?', answer: 'Yes' },
  { text: 'Have you ever seen a bus?', answer: 'Yes' },
  { text: 'Have you ever seen a bicycle?', answer: 'Yes' },
];

// Outcome default: every scored question comes out clear except the LAST
// pertinent question, which is always the high-risk one. That is the shape of
// every demo in the library (GGGR / GGGGR) — the risk lands on the final
// question so the video builds to it.
const outcomeFor = (pqOrdinal, pqCount) => (pqOrdinal === pqCount ? 'R' : 'G');

const PAUSE = { intro: 1500, question: 1500, answer: 2000, transition: 1500, closing: 400 };

/**
 * The iteration-1 / iteration-2 ask order for a production demo.
 * Returns [{ qtype, q_key, n }] — n is the per-type ordinal (PQ1, NQ3, …).
 */
function productionOrder(pqCount) {
  const order = [
    { qtype: 'NQ', n: 1 },
    { qtype: 'NQ', n: 2 },
  ];
  for (let i = 1; i <= pqCount; i++) {
    order.push({ qtype: 'PQ', n: i });
    order.push({ qtype: 'NQ', n: i + 2 });
  }
  return order.map((q) => ({ ...q, q_key: `${q.qtype}${q.n}` }));
}

function questionText(q, pqCount) {
  if (q.qtype === 'NQ') {
    const nq = NEUTRAL_QUESTIONS[(q.n - 1) % NEUTRAL_QUESTIONS.length];
    return { text: nq.text, answer: nq.answer };
  }
  return {
    text: `Pertinent question ${q.n} of ${pqCount} — replace this with your question.`,
    answer: 'No',
  };
}

/**
 * Build the segment list for a template.
 *
 * `voices` supplies the narrator/answer voice ids to stamp on each segment
 * (falling back to whatever the project already had, then to nothing — the UI
 * resolves an empty voice to its default on load).
 */
function buildSegments({ template, count, voices = {} }) {
  const narrator = voices.narrator || '';
  const answerVoice = voices.answer || narrator || '';
  const narratorLang = voices.narratorLang || 'en-US';
  const answerLang = voices.answerLang || narratorLang;

  const base = {
    effect: 'default', master_volume: 0, master_speed: 0, master_pitch: 0,
    qtype: '', q_key: '', iteration: 0, scored: 0, result: '',
  };
  const narrate = (label, role, text) => ({
    ...base, label, role, text,
    voice_id: narrator, language_code: narratorLang,
    pause_after_ms: PAUSE[role] ?? 400,
  });
  const segs = [];

  // A question + its answer always travel together — every question in both
  // formats has an answer segment.
  const askPair = ({ label, text, answer, qtype, q_key, iteration, scored, result }) => {
    segs.push({
      ...base, label, role: 'question', text,
      qtype, q_key, iteration, scored: scored ? 1 : 0,
      // Only a scored ask carries an outcome; an unscored one has no badge to post.
      result: scored ? (result || 'G') : '',
      voice_id: narrator, language_code: narratorLang, pause_after_ms: PAUSE.question,
    });
    segs.push({
      ...base, label: `${label} — answer`, role: 'answer', text: answer,
      qtype, q_key, iteration, scored: 0,
      voice_id: answerVoice, language_code: answerLang, pause_after_ms: PAUSE.answer,
    });
  };

  if (template === 'sales') {
    segs.push(narrate('Intro', 'intro', INTRO_TEXT));
    for (let i = 1; i <= count; i++) {
      askPair({
        label: `PQ${i}`,
        text: `Pertinent question ${i} of ${count} — replace this with your question.`,
        answer: 'No', qtype: 'PQ', q_key: `PQ${i}`, iteration: 1, scored: 1,
        result: outcomeFor(i, count),
      });
    }
    segs.push(narrate('Closing', 'closing', CLOSING_TEXT));
    return segs;
  }

  if (template === 'production') {
    const order = productionOrder(count);
    segs.push(narrate('Intro', 'intro', INTRO_TEXT));
    for (const iteration of [1, 2]) {
      if (iteration === 2) segs.push(narrate('Second iteration', 'transition', TRANSITION_TEXT));
      for (const q of order) {
        const { text, answer } = questionText(q, count);
        askPair({
          label: `IT${iteration} · ${q.q_key}`,
          text, answer, qtype: q.qtype, q_key: q.q_key, iteration,
          // The whole point of the format: only iteration-2 pertinent questions score.
          scored: iteration === 2 && q.qtype === 'PQ',
          result: q.qtype === 'PQ' ? outcomeFor(q.n, count) : 'G',
        });
      }
    }
    segs.push(narrate('Closing', 'closing', CLOSING_TEXT));
    return segs;
  }

  throw new Error(`unknown template: ${template}`);
}

// Question counts each format supports, and what the result is, for the UI.
const TEMPLATES = {
  sales: {
    id: 'sales',
    label: 'Sales demo',
    counts: [3, 4, 5],
    describe: (n) => `Intro · ${n} questions (all scored) · closing`,
  },
  production: {
    id: 'production',
    label: 'Production demo',
    counts: [3, 4],
    describe: (n) =>
      `Intro · ${n} pertinent + ${n + 2} neutral asked twice · transition · closing`
      + ` — ${n} scored columns`,
  },
};

module.exports = { TEMPLATES, buildSegments, productionOrder, INTRO_TEXT, TRANSITION_TEXT, CLOSING_TEXT };
