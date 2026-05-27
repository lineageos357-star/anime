// server.js – lightweight Express wrapper around `shirayuki-anime-scraper-api`
// This file can be deployed directly on Vercel. Vercel will treat the exported
// `app` as a serverless function when you add a `vercel.json` that rewrites
// the root path to this file, or you can place this file under the `api/`
// directory (e.g. `api/index.js`). The code below works in both environments.

const express = require('express');
const cors = require('cors');
const scraper = require('shirayuki-anime-scraper-api');

const app = express();
app.use(cors());

// ------------------------------------------------------------
// Search endpoint – GET /search/:query/:page?
// ------------------------------------------------------------
app.get('/search/:query/:page?', async (req, res) => {
  const { query, page = '1' } = req.params;
  try {
    const result = await scraper.search(query, parseInt(page, 10));
    res.json(result);
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message });
  }
});

// ------------------------------------------------------------
// Episode details – GET /episode/:id
// ------------------------------------------------------------
app.get('/episode/:id', async (req, res) => {
  try {
    const result = await scraper.episode(req.params.id);
    res.json(result);
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message });
  }
});

// ------------------------------------------------------------
// Server list for a given episode – GET /server/:id
// ------------------------------------------------------------
app.get('/server/:id', async (req, res) => {
  try {
    const result = await scraper.servers(req.params.id);
    res.json(result);
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message });
  }
});

// ------------------------------------------------------------
// Source extraction – GET /src-server/:id
// The original Flutter client expects a `{ restres: … }` wrapper, so we
// preserve that shape for compatibility.
// ------------------------------------------------------------
app.get('/src-server/:id', async (req, res) => {
  try {
    const result = await scraper.source(req.params.id);
    res.json({ restres: result });
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message });
  }
});

// ------------------------------------------------------------
// Related/info endpoint – GET /related/:id
// ------------------------------------------------------------
app.get('/related/:id', async (req, res) => {
  try {
    const result = await scraper.info(req.params.id);
    res.json(result);
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message });
  }
});

// ------------------------------------------------------------
// Start the server (used when running locally). Vercel will ignore this
// because it runs the exported `app` as a serverless function.
// ------------------------------------------------------------
const PORT = process.env.PORT || 3000;
if (process.env.NODE_ENV !== 'production') {
  app.listen(PORT, () => console.log(`Anime scraper API listening on ${PORT}`));
}

// Export the Express app for Vercel serverless deployment
module.exports = app;
