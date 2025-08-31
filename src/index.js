import { Client, GatewayIntentBits, Events } from "discord.js";
import fs from "node:fs";

const tokenPath = process.env.DISCORD_TOKEN_FILE || "";
const token = tokenPath && fs.existsSync(tokenPath)
  ? fs.readFileSync(tokenPath, "utf8").trim()
  : process.env.DISCORD_TOKEN;

if (!token) {
  console.error("Missing Discord token");
  process.exit(1);
}

const client = new Client({ intents: [GatewayIntentBits.Guilds] });

client.once(Events.ClientReady, () => {
  console.log(`Ready as ${client.user.tag}`);
});

client.on(Events.InteractionCreate, async interaction => {
  if (!interaction.isChatInputCommand()) return;
  if (interaction.commandName === "ping") {
    await interaction.reply("pong");
  }
});

client.login(token).catch(err => {
  console.error("Login failed:", err);
  process.exit(1);
});
