FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY src ./src
ENV NODE_ENV=production
# run as non-root
RUN adduser -D bot && chown -R bot:bot /app
USER bot
HEALTHCHECK --interval=30s --timeout=5s CMD npm run health || exit 1
CMD ["npm","start"]
