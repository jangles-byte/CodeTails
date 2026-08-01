/* CodeTails skins.
   Every skin is just a bag of CSS custom properties, so a custom one you build
   in the tuner is a first-class citizen — same shape, saved to config.json. */

window.CT_TOKENS = [
  ['bg',        'Background'],
  ['bg2',       'Panel'],
  ['bg3',       'Raised'],
  ['border',    'Border'],
  ['borderSoft','Hairline'],
  ['text',      'Text'],
  ['dim',       'Dim text'],
  ['faint',     'Faint text'],
  ['accent',    'Accent'],
  ['accent2',   'Accent light'],
  ['ok',        'Success'],
  ['warn',      'Warning'],
  ['err',       'Error'],
  ['info',      'Info'],
  ['think',     'Thinking'],
  ['user',      'Your text'],
];

window.CT_THEMES = {
  clay: {
    label: 'Clay', note: 'the default terminal',
    bg: '#000000', bg2: '#08080a', bg3: '#101014',
    border: '#26262b', borderSoft: '#181820',
    text: '#e8e6e3', dim: '#8a8a90', faint: '#55555c',
    accent: '#d97757', accent2: '#eda98d',
    ok: '#6cc08a', warn: '#d9a441', err: '#d2564b',
    info: '#6fa8dc', think: '#9b8cd8', user: '#d5d3d0',
  },
  graphite: {
    label: 'Graphite', note: 'cold, quiet, precise',
    bg: '#000000', bg2: '#0a0b0d', bg3: '#111317',
    border: '#232830', borderSoft: '#161a20',
    text: '#e6eaef', dim: '#848d99', faint: '#4f5761',
    accent: '#8fb8dd', accent2: '#b9d4ec',
    ok: '#79c39b', warn: '#d3b06a', err: '#d1706a',
    info: '#8fb8dd', think: '#9aa4d8', user: '#cfd6de',
  },
  phosphor: {
    label: 'Phosphor', note: 'green screen, 1983',
    bg: '#000300', bg2: '#030d06', bg3: '#06150c',
    border: '#124a28', borderSoft: '#0a2a17',
    text: '#c9f7d8', dim: '#4f9f70', faint: '#2d6446',
    accent: '#3ce08c', accent2: '#8bf3ba',
    ok: '#3ce08c', warn: '#d8d05a', err: '#ff6b5a',
    info: '#5ad9c0', think: '#7fd8a0', user: '#a7edc2',
  },
  amber: {
    label: 'Amber', note: 'warm CRT glow',
    bg: '#040200', bg2: '#0d0803', bg3: '#160e05',
    border: '#4a3110', borderSoft: '#2a1c09',
    text: '#ffd7a0', dim: '#b3803c', faint: '#6d4c22',
    accent: '#ffab3d', accent2: '#ffcb85',
    ok: '#c2c04a', warn: '#ffab3d', err: '#ff6a4a',
    info: '#e0b25f', think: '#d19a5c', user: '#f5c98c',
  },
  iceberg: {
    label: 'Iceberg', note: 'deep water blue',
    bg: '#000308', bg2: '#040c15', bg3: '#08131f',
    border: '#173147', borderSoft: '#0d1f2e',
    text: '#dce9f6', dim: '#6f8ba6', faint: '#40566c',
    accent: '#5cc0ff', accent2: '#9bd9ff',
    ok: '#5fd0b0', warn: '#e0b45f', err: '#ff7a72',
    info: '#5cc0ff', think: '#8fa8ff', user: '#c6dbef',
  },
  synth: {
    label: 'Synth', note: 'neon, after midnight',
    bg: '#04000a', bg2: '#0b0417', bg3: '#140a24',
    border: '#33165a', borderSoft: '#1e0d36',
    text: '#f1e6ff', dim: '#9a7fc4', faint: '#63498c',
    accent: '#ff5fd2', accent2: '#ff9ee6',
    ok: '#5df2c8', warn: '#ffcb5f', err: '#ff5f7a',
    info: '#6be7ff', think: '#b18dff', user: '#e2d1f7',
  },
  moss: {
    label: 'Moss', note: 'forest floor',
    bg: '#000200', bg2: '#070c07', bg3: '#0d150d',
    border: '#26361f', borderSoft: '#161f13',
    text: '#e3ead9', dim: '#87996f', faint: '#526046',
    accent: '#9ec96a', accent2: '#c3e29b',
    ok: '#7fc98a', warn: '#d8bc5f', err: '#d2705f',
    info: '#7fbfa8', think: '#a9b98a', user: '#cfd9c0',
  },
  paper: {
    label: 'Paper', note: 'daylight, on the porch',
    bg: '#f6f4f0', bg2: '#fffefb', bg3: '#f0ede7',
    border: '#ddd7cd', borderSoft: '#eae5dc',
    text: '#23201c', dim: '#6c6558', faint: '#9b9384',
    accent: '#c05f3c', accent2: '#a54f30',
    ok: '#3f8a5c', warn: '#a97a20', err: '#b0453a',
    info: '#3d6f9e', think: '#6b5aa8', user: '#3a352e',
  },
};

window.CT_TEXTURES = ['none', 'vignette', 'scanlines', 'grain', 'grid'];
