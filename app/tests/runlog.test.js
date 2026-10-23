import { test } from 'node:test';
import assert from 'node:assert/strict';

import { answerError, commandArgs, commandLine, countsText, durationText, formErrors, inlineParts, isActive,
  latestResult, lineKind, optionDefaults, optionLabel, optionValue, parseNumbers, requestOptions } from '../js/study/runlog.js';

// The live-drone entry as scripts/catalogue.py gives it (options shortened).
const ENTRY = {
  id: 'live-drone', script: 'characterise.py', test: 'l1', options: [
    {name: 'seconds', flag: '--seconds', kind: 'float', default: null, script_default: null, required: false},
    {name: 'distance', flag: '--distance', kind: 'float', default: 1.5, script_default: 2.0, required: false},
    {name: 'positions', flag: '--positions', kind: 'text', default: '0,0;15,0;-15,0', script_default: '-30,0;0,0', required: false},
    {name: 'truth', flag: '--truth', kind: 'choice', choices: ['array', 'measured'], default: 'array', script_default: 'array', required: false},
    {name: 'trials', flag: '--trials', kind: 'int', default: 5, script_default: 5, required: false},
    {name: 'real_drone', flag: '--real-drone', kind: 'flag', default: false, script_default: false, required: false},
  ],
};

test('the form starts at the plan values, as input texts', () => {
  assert.deepEqual(optionDefaults(ENTRY), {seconds: '', distance: '1.5', positions: '0,0;15,0;-15,0', truth: 'array', trials: '5', real_drone: false});
  assert.equal(optionLabel('burst_at'), 'Burst at');
});

test('arguments leave out the script defaults, as study_runner.arguments', () => {
  const values = optionDefaults(ENTRY);
  assert.deepEqual(commandArgs(ENTRY, values), ['l1', '--distance', '1.5', '--positions', '0,0;15,0;-15,0']);
  assert.deepEqual(commandArgs(ENTRY, {...values, positions: '-30,0;30,0', trials: '3', real_drone: true, distance: '2'}),
    ['l1', '--positions=-30,0;30,0', '--trials', '3', '--real-drone']);
  assert.equal(commandLine(ENTRY, values, 'bench_2026-10-23/live-drone'),
    'python scripts/characterise.py l1 --distance 1.5 --positions "0,0;15,0;-15,0" --out bench_2026-10-23/live-drone');
  assert.deepEqual(requestOptions(ENTRY, {...values, distance: '1,25'}),
    {seconds: null, distance: 1.25, positions: '0,0;15,0;-15,0', truth: 'array', trials: 5, real_drone: false});
});

test('option values follow the backend rules', () => {
  const [seconds, , positions, truth, trials] = ENTRY.options;
  assert.deepEqual(optionValue(seconds, ' 2,5 '), {value: 2.5});
  assert.ok(optionValue(seconds, '1e9').error);
  assert.ok(optionValue(seconds, 'abc').error);
  assert.ok(optionValue(trials, '2.5').error);
  assert.ok(optionValue(positions, '..\\file').error);
  assert.ok(optionValue(positions, 'a/b').error);
  assert.ok(optionValue(truth, 'other').error);
  assert.deepEqual(optionValue({...seconds, required: true}, ''), {error: 'Needs a value'});
  assert.deepEqual(formErrors(ENTRY, {...optionDefaults(ENTRY), trials: '2.5', distance: 'x'}),
    {trials: 'Type a whole number', distance: 'Type a number'});
});

test('answers are numbers as the scripts read them', () => {
  assert.deepEqual(parseNumbers('-98,5 172 0', 3), [-98.5, 172, 0]);
  assert.deepEqual(parseNumbers('1;2;3', 3), [1, 2, 3]);
  assert.deepEqual(parseNumbers('72,5', 1), [72.5]);
  assert.equal(parseNumbers('1 2', 3), null);
  const prompt = {text: 'Measured right, forward, up in cm', numbers: 3, optional: true};
  assert.equal(answerError(prompt, ''), null);
  assert.ok(answerError({...prompt, optional: false}, ''));
  assert.ok(answerError(prompt, '1 2'));
  assert.equal(answerError({text: 'Enter when ready.', numbers: 0, optional: false}, ''), null);
});

test('log lines show the check results, questions and answers', () => {
  assert.equal(lineKind({kind: 'out', text: 'PASS  boot checks (I)  DSP 79/79'}), 'pass');
  assert.equal(lineKind({kind: 'out', text: 'FAIL  linear in 6 dB steps'}), 'fail');
  assert.equal(lineKind({kind: 'out', text: 'INFO  N2 floor at 0,0'}), 'info');
  assert.equal(lineKind({kind: 'out', text: 'Traceback (most recent call last):'}), 'warn');
  assert.equal(lineKind({kind: 'out', text: '  cycle 1, azimuth -60.0'}), 'plain');
  assert.equal(lineKind({kind: 'ask', text: 'Enter when ready.'}), 'ask');
});

test('plan text keeps bold, code and link text', () => {
  assert.deepEqual(inlineParts('**Live log (`l7`).** Not a [scored](x.md) test, `--burst`'), [
    {kind: 'bold', parts: [{kind: 'text', text: 'Live log ('}, {kind: 'code', text: 'l7'}, {kind: 'text', text: ').'}]},
    {kind: 'text', text: ' Not a '}, {kind: 'text', text: 'scored'}, {kind: 'text', text: ' test, '}, {kind: 'code', text: '--burst'},
  ]);
});

test('labels and the latest result of a test', () => {
  assert.equal(countsText({pass: 12, fail: 1, info: 0}), '12 pass · 1 fail');
  assert.equal(durationText(125000), '2 min 05 s');
  assert.ok(isActive('stopping') && !isActive('done'));
  const results = [
    {folder: 'health_2', entry: null}, {folder: 'noise-mics', entry: 'noise-mics'}, {folder: 'live-log-new-fw', entry: null},
  ];
  assert.equal(latestResult(results, 'health').folder, 'health_2');
  assert.equal(latestResult(results, 'live-log'), null);
});
