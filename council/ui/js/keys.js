// How to get each API key, written for someone who has never made one: what
// it's for, what it costs, and each step with a link. Used by Settings ->
// API keys and by the setup wizard on the Run page (welcome.js).
// `seat` is the seat that sits out without the key.
const KEY_GUIDES = [
  {
    name: 'anthropic_api_key',
    label: 'Anthropic (Claude)',
    tag: 'Best quality',
    group: 'ai',
    purpose: 'The strongest answers the Council can give. Paid: a typical run costs well under a dollar.',
    placeholder: 'Paste key (sk-ant-…)',
    cost: 'Pay as you go; $5 of credit is plenty to start. A Claude.ai subscription doesn\'t include this. API credit is bought separately.',
    steps: [
      { text: 'Create a free account on the Anthropic Console.', link: 'https://console.anthropic.com/', linkText: 'Open Anthropic Console' },
      { text: 'Add credit to your account under Billing.', link: 'https://console.anthropic.com/settings/billing', linkText: 'Open Billing' },
      { text: 'Go to API Keys, click "Create Key", and give it any name, like "council".', link: 'https://console.anthropic.com/settings/keys', linkText: 'Open API Keys' },
      { text: 'Copy the key (it starts with sk-ant-) and paste it into the box. It\'s only shown once.' },
    ],
  },
  {
    name: 'google_api_key',
    label: 'Google (Gemini)',
    tag: 'Free',
    group: 'ai',
    purpose: 'Free AI answers, no card needed. Weaker than Claude. With only free keys, Free Mode turns on by itself so every seat uses them.',
    placeholder: 'Paste key (AQ.…)',
    cost: 'Free within Google\'s daily limits, no card needed. Stay free by not adding billing to the Google project the key belongs to. On the free tier, Google may use what you send to improve its products.',
    steps: [
      { text: 'Open Google AI Studio and sign in with any Google account.', link: 'https://aistudio.google.com/', linkText: 'Open AI Studio' },
      { text: 'Go to "Get API key" and click "Create API key". If it asks about a project, let it create one.', link: 'https://aistudio.google.com/apikey', linkText: 'Open API keys' },
      { text: 'Copy the key (it starts with AQ.) and paste it into the box.' },
    ],
  },
  {
    name: 'groq_api_key',
    label: 'Groq',
    tag: 'Free backup',
    group: 'optional',
    purpose: 'More free runs per day: when Gemini\'s free daily limit runs out mid-run, the Council switches to Groq\'s free models.',
    placeholder: 'Paste key (gsk_…)',
    cost: 'Free within daily limits, no card needed.',
    steps: [
      { text: 'Open the Groq Console and sign in (Google, GitHub or email).', link: 'https://console.groq.com/', linkText: 'Open Groq Console' },
      { text: 'Go to API Keys and click "Create API Key".', link: 'https://console.groq.com/keys', linkText: 'Open API Keys' },
      { text: 'Copy the key (it starts with gsk_) and paste it into the box. It\'s only shown once.' },
    ],
  },
  {
    name: 'openai_api_key',
    label: 'OpenAI (GPT)',
    group: 'optional',
    purpose: 'Some seats are set to use OpenAI models. Without this key those seats use another model you have a key for.',
    placeholder: 'Paste key (sk-…)',
    cost: 'Pay as you go. A ChatGPT subscription doesn\'t include this. API credit is bought separately.',
    steps: [
      { text: 'Sign in, or create an account, on the OpenAI Platform.', link: 'https://platform.openai.com/', linkText: 'Open OpenAI Platform' },
      { text: 'Add credit under Billing.', link: 'https://platform.openai.com/settings/organization/billing/overview', linkText: 'Open Billing' },
      { text: 'Go to API keys and click "Create new secret key".', link: 'https://platform.openai.com/api-keys', linkText: 'Open API keys' },
      { text: 'Copy the key (it starts with sk-) and paste it into the box. It\'s only shown once.' },
    ],
  },
  {
    name: 'fred_api_key',
    label: 'FRED (economic data)',
    tag: 'Free',
    group: 'optional',
    purpose: 'Interest rates, inflation and jobs data for the Economy seat. Without it, that seat sits out.',
    seat: 'macro_sage',
    placeholder: 'Paste key',
    cost: 'Free.',
    steps: [
      { text: 'Create a free FRED account (run by the St. Louis Fed) and sign in.', link: 'https://fredaccount.stlouisfed.org/apikeys', linkText: 'Open FRED API keys' },
      { text: 'Click "Request API Key", write one line about what it\'s for (e.g. "personal stock research"), and submit.' },
      { text: 'Copy the 32-character key and paste it into the box.' },
    ],
  },
  {
    name: 'alpha_vantage_api_key',
    label: 'Alpha Vantage (congress trades)',
    tag: 'Free',
    group: 'optional',
    purpose: 'Stock trades disclosed by members of the House and Senate, for the Congress Trades seat. Without it, that seat sits out.',
    seat: 'senate_watcher',
    placeholder: 'Paste key',
    cost: 'Free: 25 requests a day, and each run uses at most one (repeat runs on the same stock reuse it for 6 hours).',
    steps: [
      { text: 'Open Alpha Vantage\'s free key page.', link: 'https://www.alphavantage.co/support/#api-key', linkText: 'Open Alpha Vantage' },
      { text: 'Fill in the short form (choose "Investor" or "Student", any organisation name) and click "GET FREE API KEY".' },
      { text: 'Copy the key it shows and paste it into the box.' },
    ],
  },
];
const KEY_GUIDE_BY_NAME = Object.fromEntries(KEY_GUIDES.map(g => [g.name, g]));
