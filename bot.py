import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import os
import asyncio
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from pathlib import Path
import aiohttp

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
TOKEN = os.getenv("DISCORD_TOKEN")

# ── Allowed user IDs ──────────────────────────────────────────────────────────
ALLOWED_USERS = {
    123456789012345678,  # Replace with your Discord user ID
    # Add friends' IDs here
}

# ── Categories ────────────────────────────────────────────────────────────────
CATEGORIES = ["ML", "DAA", "DA", "DM", "ESS"]

# ── Upload folders ────────────────────────────────────────────────────────────
BASE_UPLOAD_DIR = "uploads"
for cat in CATEGORIES:
    os.makedirs(os.path.join(BASE_UPLOAD_DIR, cat), exist_ok=True)

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
scheduler = AsyncIOScheduler()


# ─────────────────────────────────────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect("database.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT, task TEXT, completed INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT, channel_id TEXT,
                remind_at TEXT, message TEXT, fired INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT, category TEXT, title TEXT,
                filename TEXT, filepath TEXT, uploaded_at TEXT
            )
        """)
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
#  VIEWS  (Dropdowns & Modals)
# ─────────────────────────────────────────────────────────────────────────────

class CategorySelect(discord.ui.Select):
    """Dropdown to pick a category."""
    def __init__(self, purpose: str, extra=None):
        self.purpose = purpose  # "upload" or "retrieve"
        self.extra = extra
        options = [discord.SelectOption(label=cat, value=cat) for cat in CATEGORIES]
        super().__init__(placeholder="Choose a category...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        category = self.values[0]
        if self.purpose == "upload":
            modal = TitleModal(category=category, extra=self.extra)
            await interaction.response.send_modal(modal)
        elif self.purpose == "retrieve":
            await interaction.response.defer(ephemeral=True)
            await show_file_list(interaction, category)
        self.view.stop()


class CategoryView(discord.ui.View):
    def __init__(self, purpose: str, extra=None):
        super().__init__(timeout=60)
        self.add_item(CategorySelect(purpose=purpose, extra=extra))


class TitleModal(discord.ui.Modal, title="File Title"):
    file_title = discord.ui.TextInput(
        label="Enter a title for this file",
        placeholder="e.g. Week 3 ML Notes",
        max_length=100
    )

    def __init__(self, category: str, extra: dict):
        super().__init__()
        self.category = category
        self.extra = extra  # {url, filename, channel_id}

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        title = self.file_title.value.strip()
        category = self.category
        filename = self.extra["filename"]
        url = self.extra["url"]

        # Save to disk
        save_dir = os.path.join(BASE_UPLOAD_DIR, category)
        save_path = os.path.join(save_dir, filename)
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(save_path):
            save_path = os.path.join(save_dir, f"{base}_{counter}{ext}")
            counter += 1

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    with open(save_path, "wb") as f:
                        f.write(await resp.read())

        async with aiosqlite.connect("database.db") as db:
            await db.execute(
                "INSERT INTO files (user_id, category, title, filename, filepath, uploaded_at) VALUES (?,?,?,?,?,?)",
                (str(interaction.user.id), category, title, os.path.basename(save_path), save_path, datetime.now().isoformat())
            )
            await db.commit()

        embed = discord.Embed(title="✅ File Saved!", color=discord.Color.green())
        embed.add_field(name="Title", value=title, inline=True)
        embed.add_field(name="Category", value=category, inline=True)
        embed.add_field(name="Saved as", value=os.path.basename(save_path), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────────────────────────────────────
#  FILE RETRIEVAL
# ─────────────────────────────────────────────────────────────────────────────

async def show_file_list(interaction: discord.Interaction, category: str):
    async with aiosqlite.connect("database.db") as db:
        async with db.execute(
            "SELECT id, title, filename, uploaded_at FROM files WHERE user_id=? AND category=? ORDER BY uploaded_at DESC",
            (str(interaction.user.id), category)
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await interaction.followup.send(f"📭 No files in **{category}**.", ephemeral=True)
        return

    options = [
        discord.SelectOption(
            label=title[:100],
            value=str(fid),
            description=f"{filename} — {uploaded_at[:10]}"
        )
        for fid, title, filename, uploaded_at in rows
    ]

    view = FilePickView(options)
    embed = discord.Embed(
        title=f"📁 {category} Files",
        description="Select a file to download:",
        color=discord.Color.green()
    )
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class FilePickSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="Choose a file...", options=options)

    async def callback(self, interaction: discord.Interaction):
        file_id = int(self.values[0])
        async with aiosqlite.connect("database.db") as db:
            async with db.execute(
                "SELECT filepath, title FROM files WHERE id=? AND user_id=?",
                (file_id, str(interaction.user.id))
            ) as cursor:
                row = await cursor.fetchone()

        if not row or not os.path.exists(row[0]):
            await interaction.response.send_message("❌ File not found on disk.", ephemeral=True)
            return

        filepath, title = row
        await interaction.response.send_message(
            f"📤 Here's **{title}**:",
            file=discord.File(filepath),
            ephemeral=True
        )
        self.view.stop()


class FilePickView(discord.ui.View):
    def __init__(self, options):
        super().__init__(timeout=60)
        self.add_item(FilePickSelect(options))


# ─────────────────────────────────────────────────────────────────────────────
#  BOT EVENTS
# ─────────────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await init_db()
    await restore_reminders()
    scheduler.start()
    try:
        synced = await tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Sync error: {e}")
    print(f"✅ Bot online as {bot.user}")


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.author.id not in ALLOWED_USERS:
        return

    if message.attachments:
        attachment = message.attachments[0]
        embed = discord.Embed(
            title="📂 File Detected",
            description=f"**{attachment.filename}**\n\nPick a category below:",
            color=discord.Color.blurple()
        )
        view = CategoryView(
            purpose="upload",
            extra={
                "url": attachment.url,
                "filename": attachment.filename,
                "channel_id": message.channel.id
            }
        )
        await message.channel.send(embed=embed, view=view)

    await bot.process_commands(message)


# ─────────────────────────────────────────────────────────────────────────────
#  SLASH COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

def allowed(interaction: discord.Interaction) -> bool:
    return interaction.user.id in ALLOWED_USERS


@tree.command(name="getfile", description="Browse and retrieve your uploaded files")
async def getfile(interaction: discord.Interaction):
    if not allowed(interaction):
        await interaction.response.send_message("❌ Not authorised.", ephemeral=True)
        return
    view = CategoryView(purpose="retrieve")
    embed = discord.Embed(title="📁 Retrieve File", description="Select a category:", color=discord.Color.gold())
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@tree.command(name="myfiles", description="List all your uploaded files")
async def myfiles(interaction: discord.Interaction):
    if not allowed(interaction):
        await interaction.response.send_message("❌ Not authorised.", ephemeral=True)
        return

    async with aiosqlite.connect("database.db") as db:
        async with db.execute(
            "SELECT id, category, title, filename, uploaded_at FROM files WHERE user_id=? ORDER BY category, uploaded_at DESC",
            (str(interaction.user.id),)
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await interaction.response.send_message("📭 No files uploaded yet.", ephemeral=True)
        return

    embed = discord.Embed(title="📚 Your Files", color=discord.Color.blurple())
    grouped: dict[str, list] = {}
    for fid, cat, title, filename, uploaded_at in rows:
        grouped.setdefault(cat, []).append(f"• **[{fid}]** {title} — `{filename}` ({uploaded_at[:10]})")
    for cat, lines in grouped.items():
        embed.add_field(name=f"📂 {cat}", value="\n".join(lines), inline=False)
    embed.set_footer(text="Use /deletefile with the [ID] to remove a file")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="deletefile", description="Delete one of your uploaded files")
@app_commands.describe(file_id="File ID shown in /myfiles (the number in brackets)")
async def deletefile(interaction: discord.Interaction, file_id: int):
    if not allowed(interaction):
        await interaction.response.send_message("❌ Not authorised.", ephemeral=True)
        return

    async with aiosqlite.connect("database.db") as db:
        async with db.execute(
            "SELECT filepath, title FROM files WHERE id=? AND user_id=?",
            (file_id, str(interaction.user.id))
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message("❌ File not found.", ephemeral=True)
            return

        filepath, title = row
        if os.path.exists(filepath):
            os.remove(filepath)
        await db.execute("DELETE FROM files WHERE id=?", (file_id,))
        await db.commit()

    await interaction.response.send_message(f"🗑️ Deleted **{title}**.", ephemeral=True)


@tree.command(name="todo_add", description="Add a todo task")
@app_commands.describe(task="The task to add")
async def todo_add(interaction: discord.Interaction, task: str):
    if not allowed(interaction):
        await interaction.response.send_message("❌ Not authorised.", ephemeral=True)
        return
    async with aiosqlite.connect("database.db") as db:
        await db.execute("INSERT INTO todos (user_id, task) VALUES (?,?)", (str(interaction.user.id), task))
        await db.commit()
    await interaction.response.send_message(f"✅ Added: **{task}**", ephemeral=True)


@tree.command(name="todo_list", description="View your todo list")
async def todo_list(interaction: discord.Interaction):
    if not allowed(interaction):
        await interaction.response.send_message("❌ Not authorised.", ephemeral=True)
        return
    async with aiosqlite.connect("database.db") as db:
        async with db.execute(
            "SELECT id, task, completed FROM todos WHERE user_id=?", (str(interaction.user.id),)
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await interaction.response.send_message("📭 No todos yet.", ephemeral=True)
        return

    embed = discord.Embed(title="📋 Your Todos", color=discord.Color.blurple())
    for tid, task, done in rows:
        embed.add_field(name=f"{'✅' if done else '⬜'} #{tid}", value=task, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="todo_done", description="Mark a todo as done")
@app_commands.describe(todo_id="The todo ID from /todo_list")
async def todo_done(interaction: discord.Interaction, todo_id: int):
    if not allowed(interaction):
        await interaction.response.send_message("❌ Not authorised.", ephemeral=True)
        return
    async with aiosqlite.connect("database.db") as db:
        await db.execute(
            "UPDATE todos SET completed=1 WHERE id=? AND user_id=?", (todo_id, str(interaction.user.id))
        )
        await db.commit()
    await interaction.response.send_message(f"✅ Todo #{todo_id} marked done!", ephemeral=True)


@tree.command(name="todo_delete", description="Delete a todo")
@app_commands.describe(todo_id="The todo ID from /todo_list")
async def todo_delete(interaction: discord.Interaction, todo_id: int):
    if not allowed(interaction):
        await interaction.response.send_message("❌ Not authorised.", ephemeral=True)
        return
    async with aiosqlite.connect("database.db") as db:
        await db.execute("DELETE FROM todos WHERE id=? AND user_id=?", (todo_id, str(interaction.user.id)))
        await db.commit()
    await interaction.response.send_message(f"🗑️ Todo #{todo_id} deleted.", ephemeral=True)


@tree.command(name="remind", description="Set a reminder")
@app_commands.describe(date="Date in YYYY-MM-DD format", time="Time in HH:MM format", message="What to remind you about")
async def remind(interaction: discord.Interaction, date: str, time: str, message: str):
    if not allowed(interaction):
        await interaction.response.send_message("❌ Not authorised.", ephemeral=True)
        return
    try:
        remind_at = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    except ValueError:
        await interaction.response.send_message("❌ Use format: date=`2026-03-01` time=`18:30`", ephemeral=True)
        return

    async with aiosqlite.connect("database.db") as db:
        cursor = await db.execute(
            "INSERT INTO reminders (user_id, channel_id, remind_at, message) VALUES (?,?,?,?)",
            (str(interaction.user.id), str(interaction.channel_id), remind_at.isoformat(), message)
        )
        reminder_id = cursor.lastrowid
        await db.commit()

    scheduler.add_job(
        fire_reminder, "date", run_date=remind_at,
        args=[interaction.user.id, interaction.channel_id, message, reminder_id],
        id=f"reminder_{reminder_id}"
    )
    await interaction.response.send_message(
        f"⏰ Reminder set for **{date} at {time}**: {message}", ephemeral=True
    )


@tree.command(name="help", description="Show all available commands")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="🤖 Bot Commands", color=discord.Color.blurple())
    embed.add_field(name="📂 Files", value=(
        "`/getfile` — Browse & retrieve a file\n"
        "`/myfiles` — See all your uploaded files\n"
        "`/deletefile` — Delete a file by ID\n"
        "*(To upload: just attach a file in chat)*"
    ), inline=False)
    embed.add_field(name="📋 Todos", value=(
        "`/todo_add` — Add a task\n"
        "`/todo_list` — View tasks\n"
        "`/todo_done` — Mark done\n"
        "`/todo_delete` — Delete task"
    ), inline=False)
    embed.add_field(name="⏰ Reminders", value="`/remind` — Set a reminder (persists across restarts)", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────────────────────────────────────
#  REMINDER HELPERS
# ─────────────────────────────────────────────────────────────────────────────

async def fire_reminder(user_id, channel_id, message, reminder_id):
    channel = bot.get_channel(int(channel_id))
    if channel:
        await channel.send(f"⏰ <@{user_id}> Reminder: **{message}**")
    async with aiosqlite.connect("database.db") as db:
        await db.execute("UPDATE reminders SET fired=1 WHERE id=?", (reminder_id,))
        await db.commit()


async def restore_reminders():
    async with aiosqlite.connect("database.db") as db:
        async with db.execute(
            "SELECT id, user_id, channel_id, remind_at, message FROM reminders WHERE fired=0"
        ) as cursor:
            rows = await cursor.fetchall()

    now = datetime.now()
    for rid, user_id, channel_id, remind_at_str, msg in rows:
        remind_at = datetime.fromisoformat(remind_at_str)
        if remind_at <= now:
            await fire_reminder(user_id, channel_id, f"(missed) {msg}", rid)
        else:
            scheduler.add_job(
                fire_reminder, "date", run_date=remind_at,
                args=[user_id, channel_id, msg, rid],
                id=f"reminder_{rid}"
            )
    print(f"🔄 Restored {len(rows)} reminder(s)")


# ─────────────────────────────────────────────────────────────────────────────
bot.run(TOKEN)