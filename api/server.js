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
    // Wrap in expected shape
    res.json({ searchYour: result });
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
    // Expected shape: { infoX: [...] }
    res.json({ infoX: result });
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message });
  }
});

// ------------------------------------------------------------
// Duplicate routes with /api prefix for compatibility
app.get('/api/search/:query/:page?', async (req, res) => {
  const { query, page = '1' } = req.params;
  try {
    const result = await scraper.search(query, parseInt(page, 10));
    res.json(result);
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message });
  }
});

app.get('/api/episode/:id', async (req, res) => {
  try {
    const result = await scraper.episode(req.params.id);
    res.json(result);
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message });
  }
});

app.get('/api/server/:id', async (req, res) => {
  try {
    const result = await scraper.servers(req.params.id);
    res.json(result);
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message });
  }
});

app.get('/api/src-server/:id', async (req, res) => {
  try {
    const result = await scraper.source(req.params.id);
    res.json({ restres: result });
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message });
  }
});

app.get('/api/related/:id', async (req, res) => {
  try {
    const result = await scraper.info(req.params.id);
    res.json(result);
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message });
  }
});

// Dummy parse endpoint providing empty collections for trending, upcoming, slides
app.get('/api/parse', (req, res) => {
  res.json({ trend: [], UpcomingAnime: [], slides: [] });
});

// ------------------------------------------------------------
// Root endpoint – GET /
// ------------------------------------------------------------
app.get('/', (req, res) => {
  res.send('Anime Scraper API is running! Use /api/search/:query to search.');
});


// because it runs the exported `app` as a serverless function.
// ------------------------------------------------------------
const PORT = process.env.PORT || 3000;
if (process.env.NODE_ENV !== 'production') {
  app.listen(PORT, () => console.log(`Anime scraper API listening on ${PORT}`));
}

// Export the Express app for Vercel serverless deployment
module.exports = app;
