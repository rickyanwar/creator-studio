# Headless Fabric.js renderer.
# NOTE: originally built on Debian's chromium + puppeteer-core per the spec,
# but Debian's Chromium 150 SIGTRAPs whenever CDP remote debugging is enabled
# (port AND pipe transports, arm64) — so we use Playwright's own Chromium
# build instead. Same headless-Chromium + Fabric.js render, cross-arch.
FROM node:20-bookworm-slim

WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev \
    && npx playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Vendored Google Fonts (OFL) so canvas text can use them headlessly
COPY fonts/ /usr/share/fonts/truetype/custom/
RUN fc-cache -f

COPY server.js render-page.html inject.js ./

EXPOSE 3001
CMD ["node", "server.js"]
