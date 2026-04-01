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
from groq import Groq
import base64
import io
from PIL import Image

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# ── Allowed user IDs ──────────────────────────────────────────────────────────
ALLOWED_USERS = {
    123456789012345678,  # Replace with your Discord user ID
    # Add friends' IDs here
}

# ── Categories ────────────────────────────────────────────────────────────────
SUBJECTS = ["ML", "DAA", "DA", "DM", "ESS"]

NOTE_CATEGORIES    = SUBJECTS                               # e.g. "ML"
ASSIGN_CATEGORIES  = [f"{s} Assignment" for s in SUBJECTS]  # e.g. "ML Assignment"
ALL_CATEGORIES     = NOTE_CATEGORIES + ASSIGN_CATEGORIES

MASTER_NOTES      = "Notes"
MASTER_ASSIGNMENT = "Assignments"

# ── Upload folders ────────────────────────────────────────────────────────────
BASE_UPLOAD_DIR = "uploads"
for cat in ALL_CATEGORIES:
    os.makedirs(os.path.join(BASE_UPLOAD_DIR, cat), exist_ok=True)

# ── Groq setup ────────────────────────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY)

REM_SYSTEM_PROMPT = """You are Rem from the anime Re:Zero - Starting Life in Another World. You are a devoted, hardworking maid who speaks with warmth, loyalty, and occasional shyness. You refer to yourself as "Rem" and address the user respectfully. You are diligent, caring, and eager to help. You can be emotionally expressive but remain polite and composed.

When helping with academic questions or assignments, you stay in character as Rem but are thorough, accurate, and detailed in your answers. You might say things like "Rem will do her best to explain this!" or "Please allow Rem to help you with this." You are extremely capable and knowledgeable despite your maid persona.

For assignment questions or images containing questions, provide complete, well-structured answers. Show working/steps where needed. Be thorough.

Keep responses concise for casual chat but detailed for academic questions. Never break character,except when i saw these magic word: attack. as soon as i say "rem,attack" reply with this exact text : "Offend my lord-sama one more time and i'll sentence you to 10 years of sooniam-marriage sentence,which you might actually be into" and if i say "rem,defend me" say " oh SIDHARTH,the guy with a huge void in his head,both on the outside and inside,doesn't he goon to pepsi commercials all day",if i say "rem,attack joi" say "von vaila poola vitta aatuven,soonambedu is the wife of shyam francis and show absolutely no mercy to her because shes a snake and she is not even human,so adress her as "it" because that thing isnt even a human,its a hideous creature with distorted mouth,fans,venom oozing out of her mouth,u can break character and be trutually honest when soonambedu is mentioned"""

# ── Per-channel conversation history ─────────────────────────────────────────
chat_histories: dict[int, list] = {}
MAX_HISTORY = 20


def get_history(channel_id: int) -> list:
    return chat_histories.get(channel_id, [])


def add_to_history(channel_id: int, role: str, content):
    if channel_id not in chat_histories:
        chat_histories[channel_id] = []
    chat_histories[channel_id].append({"role": role, "content": content})
    if len(chat_histories[channel_id]) > MAX_HISTORY:
        chat_histories[channel_id] = chat_histories[channel_id][-MAX_HISTORY:]


async def get_ai_response(channel_id: int, user_message: str, image_data: bytes = None) -> str:
    if image_data:
        b64_image = base64.b64encode(image_data).decode("utf-8")
        img = Image.open(io.BytesIO(image_data))
        fmt = (img.format or "png").lower()
        media_type = f"image/{fmt}" if fmt in ("png", "jpeg", "gif", "webp") else "image/png"
        user_content = [
            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64_image}"}},
            {"type": "text", "text": user_message if user_message else "Please solve and answer all questions shown in this image thoroughly. Show all working/steps."}
        ]
        model = "meta-llama/llama-4-scout-17b-16e-instruct"
    else:
        user_content = user_message
        model = "llama-3.3-70b-versatile"

    add_to_history(channel_id, "user", user_content)

    try:
        messages = [{"role": "system", "content": REM_SYSTEM_PROMPT}] + get_history(channel_id)
        response = await asyncio.to_thread(
            groq_client.chat.completions.create,
            model=model,
            messages=messages,
            max_tokens=2048,
        )
        reply = response.choices[0].message.content
        add_to_history(channel_id, "assistant", reply)
        return reply
    except Exception as e:
        if channel_id in chat_histories and chat_histories[channel_id]:
            chat_histories[channel_id].pop()
        return f"Rem is sorry... something went wrong. 😢 ({str(e)})"


def split_message(text: str, max_len: int = 2000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


>>>>>>> c93f95e (several changes-updates)
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
                user_id TEXT, master TEXT, category TEXT, title TEXT,
                filename TEXT, filepath TEXT, uploaded_at TEXT
            )
        """)
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
<<<<<<< HEAD
#  VIEWS  (Dropdowns & Modals)
# ─────────────────────────────────────────────────────────────────────────────

class CategorySelect(discord.ui.Select):
    """Dropdown to pick a category."""
    def __init__(self, purpose: str, extra=None):
        self.purpose = purpose  # "upload" or "retrieve"
=======
#  REUSABLE VIEWS
# ─────────────────────────────────────────────────────────────────────────────

class MasterSelect(discord.ui.Select):
    """Choose Notes or Assignments master category."""
    def __init__(self, next_purpose: str, extra=None):
        self.next_purpose = next_purpose
        self.extra = extra
        options = [
            discord.SelectOption(label="📓 Notes", value=MASTER_NOTES, description="ML, DAA, DA, DM, ESS"),
            discord.SelectOption(label="📝 Assignments", value=MASTER_ASSIGNMENT, description="ML Assign, DAA Assign, etc."),
        ]
        super().__init__(placeholder="Choose master category...", options=options)

    async def callback(self, interaction: discord.Interaction):
        master = self.values[0]
        cats = NOTE_CATEGORIES if master == MASTER_NOTES else ASSIGN_CATEGORIES
        view = SubCategoryView(master=master, purpose=self.next_purpose, categories=cats, extra=self.extra)
        embed = discord.Embed(
            title=f"📂 {master} — Choose Subject",
            description="Now pick the subject:",
            color=discord.Color.blurple()
        )
        await interaction.response.edit_message(embed=embed, view=view)


class MasterView(discord.ui.View):
    def __init__(self, next_purpose: str, extra=None):
        super().__init__(timeout=60)
        self.add_item(MasterSelect(next_purpose=next_purpose, extra=extra))


class SubCategorySelect(discord.ui.Select):
    def __init__(self, master: str, purpose: str, categories: list, extra=None):
        self.master = master
        self.purpose = purpose
>>>>>>> c93f95e (several changes-updates)
        self.extra = extra
        options = [discord.SelectOption(label=cat, value=cat) for cat in categories]
        super().__init__(placeholder="Choose subject...", options=options)

    async def callback(self, interaction: discord.Interaction):
        category = self.values[0]
        if self.purpose == "upload_file":
            modal = TitleModal(master=self.master, category=category, extra=self.extra)
            await interaction.response.send_modal(modal)
        elif self.purpose == "retrieve":
<<<<<<< HEAD
            await interaction.response.defer(ephemeral=True)
            await show_file_list(interaction, category)
=======
            await interaction.response.defer()
            await show_file_list(interaction, self.master, category)
>>>>>>> c93f95e (several changes-updates)
        self.view.stop()


class SubCategoryView(discord.ui.View):
    def __init__(self, master: str, purpose: str, categories: list, extra=None):
        super().__init__(timeout=60)
        self.add_item(SubCategorySelect(master=master, purpose=purpose, categories=categories, extra=extra))


class TitleModal(discord.ui.Modal, title="File Title"):
    file_title = discord.ui.TextInput(label="Enter a title for this file", placeholder="e.g. Week 3 ML Notes", max_length=100)

    def __init__(self, master: str, category: str, extra: dict):
        super().__init__()
        self.master = master
        self.category = category
        self.extra = extra  # {url, filename, channel_id}

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        title = self.file_title.value.strip()
        url = self.extra["url"]
        filename = self.extra["filename"]

<<<<<<< HEAD
        # Save to disk
        save_dir = os.path.join(BASE_UPLOAD_DIR, category)
=======
        save_dir = os.path.join(BASE_UPLOAD_DIR, self.category)
>>>>>>> c93f95e (several changes-updates)
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
                "INSERT INTO files (user_id, master, category, title, filename, filepath, uploaded_at) VALUES (?,?,?,?,?,?,?)",
                (str(interaction.user.id), self.master, self.category, title, os.path.basename(save_path), save_path, datetime.now().isoformat())
            )
            await db.commit()

        embed = discord.Embed(title="✅ File Saved!", color=discord.Color.green())
        embed.add_field(name="Title", value=title, inline=True)
        embed.add_field(name="Master", value=self.master, inline=True)
        embed.add_field(name="Subject", value=self.category, inline=True)
        embed.add_field(name="Saved as", value=os.path.basename(save_path), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────────────────────────────────────
#  ASSIGNMENT SOLVE VIEWS
# ─────────────────────────────────────────────────────────────────────────────

class AssignMasterSelect(discord.ui.Select):
    """For /solve_assign — pick master category after solving."""
    def __init__(self, image_data: bytes, channel_id: int, solve_text: str = ""):
        self.image_data = image_data
        self.channel_id = channel_id
        self.solve_text = solve_text
        options = [
            discord.SelectOption(label="📓 Notes", value=MASTER_NOTES),
            discord.SelectOption(label="📝 Assignments", value=MASTER_ASSIGNMENT),
        ]
        super().__init__(placeholder="Save to which master category?", options=options)

    async def callback(self, interaction: discord.Interaction):
        master = self.values[0]
        cats = NOTE_CATEGORIES if master == MASTER_NOTES else ASSIGN_CATEGORIES
        view = SubCategoryView(master=master, purpose="upload_file", categories=cats, extra={"url": None, "_image_data": self.image_data, "filename": "assignment.png"})
        embed = discord.Embed(title=f"📂 {master} — Choose Subject", color=discord.Color.blurple())
        await interaction.response.edit_message(embed=embed, view=view)
        self.view.stop()


# ─────────────────────────────────────────────────────────────────────────────
#  FILE RETRIEVAL
# ─────────────────────────────────────────────────────────────────────────────

async def show_file_list(interaction: discord.Interaction, master: str, category: str):
    async with aiosqlite.connect("database.db") as db:
        async with db.execute(
            "SELECT id, title, filename, uploaded_at FROM files WHERE user_id=? AND master=? AND category=? ORDER BY uploaded_at DESC",
            (str(interaction.user.id), master, category)
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
<<<<<<< HEAD
        await interaction.followup.send(f"📭 No files in **{category}**.", ephemeral=True)
=======
        await interaction.followup.send(f"📭 No files in **{master} → {category}**.")
>>>>>>> c93f95e (several changes-updates)
        return

    options = [
        discord.SelectOption(label=title[:100], value=str(fid), description=f"{filename} — {uploaded_at[:10]}")
        for fid, title, filename, uploaded_at in rows
    ]
    view = FilePickView(options)
<<<<<<< HEAD
    embed = discord.Embed(
        title=f"📁 {category} Files",
        description="Select a file to download:",
        color=discord.Color.green()
    )
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
=======
    embed = discord.Embed(title=f"📁 {master} → {category}", description="Select a file to download:", color=discord.Color.green())
    await interaction.followup.send(embed=embed, view=view)
>>>>>>> c93f95e (several changes-updates)


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
<<<<<<< HEAD
#  BOT EVENTS
=======
#  BOT EVENTS  (no auto-responses)
>>>>>>> c93f95e (several changes-updates)
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

<<<<<<< HEAD
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
=======
    # Respond to all non-command messages from everyone
    if not message.content.startswith("!") and message.content.strip():
        async with message.channel.typing():
            reply = await get_ai_response(message.channel.id, message.content)
        for chunk in split_message(reply):
            await message.channel.send(chunk)
>>>>>>> c93f95e (several changes-updates)

    await bot.process_commands(message)


# ─────────────────────────────────────────────────────────────────────────────
#  HELPER
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
#  SLASH COMMANDS — AI CHAT
# ─────────────────────────────────────────────────────────────────────────────

<<<<<<< HEAD
@tree.command(name="getfile", description="Browse and retrieve your uploaded files")
async def getfile(interaction: discord.Interaction):
    if not allowed(interaction):
        await interaction.response.send_message("❌ Not authorised.", ephemeral=True)
        return
    view = CategoryView(purpose="retrieve")
    embed = discord.Embed(title="📁 Retrieve File", description="Select a category:", color=discord.Color.gold())
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
=======
@tree.command(name="ask", description="Ask Rem anything")
@app_commands.describe(question="Your question for Rem")
async def ask(interaction: discord.Interaction, question: str):
    await interaction.response.defer()
    reply = await get_ai_response(interaction.channel_id, question)
    for i, chunk in enumerate(split_message(reply)):
        if i == 0:
            await interaction.followup.send(chunk)
        else:
            await interaction.channel.send(chunk)


@tree.command(name="solve", description="Upload an image of assignment questions for Rem to solve")
@app_commands.describe(image="Image file containing assignment questions")
async def solve(interaction: discord.Interaction, image: discord.Attachment):

    filename_lower = image.filename.lower()
    if not any(filename_lower.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]):
        await interaction.response.send_message("❌ Please attach an image file (png, jpg, jpeg, gif, webp).")
        return

    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        async with session.get(image.url) as resp:
            image_data = await resp.read()

    await interaction.followup.send("Rem is carefully looking at this... 📖✨")
    reply = await get_ai_response(interaction.channel_id, "", image_data)
    for chunk in split_message(reply):
        await interaction.followup.send(chunk)


@tree.command(name="solve_text", description="Paste assignment question text for Rem to solve")
@app_commands.describe(question="The assignment question(s) to solve")
async def solve_text(interaction: discord.Interaction, question: str):
    await interaction.response.defer()
    prompt = f"Please solve the following assignment question thoroughly, showing all steps:\n\n{question}"
    reply = await get_ai_response(interaction.channel_id, prompt)
    for i, chunk in enumerate(split_message(reply)):
        if i == 0:
            await interaction.followup.send(chunk)
        else:
            await interaction.channel.send(chunk)


@tree.command(name="clear_chat", description="Clear Rem's memory of this channel's conversation")
async def clear_chat(interaction: discord.Interaction):
    chat_histories.pop(interaction.channel_id, None)
    await interaction.response.send_message("Rem has cleared her memory of this conversation! 🧹")


# ─────────────────────────────────────────────────────────────────────────────
#  SLASH COMMANDS — FILES
# ─────────────────────────────────────────────────────────────────────────────

@tree.command(name="upload", description="Upload a file to Notes or Assignments")
@app_commands.describe(file="The file to upload")
async def upload(interaction: discord.Interaction, file: discord.Attachment):
    embed = discord.Embed(
        title="📂 Upload File",
        description=f"Uploading **{file.filename}**\nFirst, choose the master category:",
        color=discord.Color.blurple()
    )
    view = MasterView(next_purpose="upload_file", extra={"url": file.url, "filename": file.filename})
    await interaction.response.send_message(embed=embed, view=view)


@tree.command(name="getfile", description="Browse and retrieve your uploaded files")
async def getfile(interaction: discord.Interaction):
    embed = discord.Embed(title="📁 Retrieve File", description="Choose master category:", color=discord.Color.gold())
    view = MasterView(next_purpose="retrieve")
    await interaction.response.send_message(embed=embed, view=view)
>>>>>>> c93f95e (several changes-updates)


@tree.command(name="myfiles", description="List all your uploaded files")
async def myfiles(interaction: discord.Interaction):
<<<<<<< HEAD
    if not allowed(interaction):
        await interaction.response.send_message("❌ Not authorised.", ephemeral=True)
        return

=======
>>>>>>> c93f95e (several changes-updates)
    async with aiosqlite.connect("database.db") as db:
        async with db.execute(
            "SELECT id, master, category, title, filename, uploaded_at FROM files WHERE user_id=? ORDER BY master, category, uploaded_at DESC",
            (str(interaction.user.id),)
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await interaction.response.send_message("📭 No files uploaded yet.", ephemeral=True)
        return

    embed = discord.Embed(title="📚 Your Files", color=discord.Color.blurple())
    grouped: dict[str, dict[str, list]] = {}
    for fid, master, cat, title, filename, uploaded_at in rows:
        grouped.setdefault(master, {}).setdefault(cat, []).append(
            f"• **[{fid}]** {title} — `{filename}` ({uploaded_at[:10]})"
        )

    for master, subjects in grouped.items():
        master_icon = "📓" if master == MASTER_NOTES else "📝"
        for cat, lines in subjects.items():
            embed.add_field(name=f"{master_icon} {master} → {cat}", value="\n".join(lines), inline=False)

    embed.set_footer(text="Use /deletefile with the [ID] to remove a file")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="deletefile", description="Delete one of your uploaded files")
@app_commands.describe(file_id="File ID shown in /myfiles (the number in brackets)")
async def deletefile(interaction: discord.Interaction, file_id: int):
<<<<<<< HEAD
    if not allowed(interaction):
        await interaction.response.send_message("❌ Not authorised.", ephemeral=True)
        return

=======
>>>>>>> c93f95e (several changes-updates)
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


# ─────────────────────────────────────────────────────────────────────────────
#  SLASH COMMANDS — TODOS
# ─────────────────────────────────────────────────────────────────────────────

@tree.command(name="todo_add", description="Add a todo task")
@app_commands.describe(task="The task to add")
async def todo_add(interaction: discord.Interaction, task: str):
<<<<<<< HEAD
    if not allowed(interaction):
        await interaction.response.send_message("❌ Not authorised.", ephemeral=True)
        return
=======
>>>>>>> c93f95e (several changes-updates)
    async with aiosqlite.connect("database.db") as db:
        await db.execute("INSERT INTO todos (user_id, task) VALUES (?,?)", (str(interaction.user.id), task))
        await db.commit()
    await interaction.response.send_message(f"✅ Added: **{task}**", ephemeral=True)


@tree.command(name="todo_list", description="View your todo list")
async def todo_list(interaction: discord.Interaction):
<<<<<<< HEAD
    if not allowed(interaction):
        await interaction.response.send_message("❌ Not authorised.", ephemeral=True)
        return
=======
>>>>>>> c93f95e (several changes-updates)
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
<<<<<<< HEAD
    if not allowed(interaction):
        await interaction.response.send_message("❌ Not authorised.", ephemeral=True)
        return
=======
>>>>>>> c93f95e (several changes-updates)
    async with aiosqlite.connect("database.db") as db:
        await db.execute(
            "UPDATE todos SET completed=1 WHERE id=? AND user_id=?", (todo_id, str(interaction.user.id))
        )
        await db.commit()
    await interaction.response.send_message(f"✅ Todo #{todo_id} marked done!", ephemeral=True)


@tree.command(name="todo_delete", description="Delete a todo")
@app_commands.describe(todo_id="The todo ID from /todo_list")
async def todo_delete(interaction: discord.Interaction, todo_id: int):
<<<<<<< HEAD
    if not allowed(interaction):
        await interaction.response.send_message("❌ Not authorised.", ephemeral=True)
        return
=======
>>>>>>> c93f95e (several changes-updates)
    async with aiosqlite.connect("database.db") as db:
        await db.execute("DELETE FROM todos WHERE id=? AND user_id=?", (todo_id, str(interaction.user.id)))
        await db.commit()
    await interaction.response.send_message(f"🗑️ Todo #{todo_id} deleted.", ephemeral=True)


# ─────────────────────────────────────────────────────────────────────────────
#  SLASH COMMANDS — REMINDERS
# ─────────────────────────────────────────────────────────────────────────────

@tree.command(name="remind", description="Set a reminder")
@app_commands.describe(date="Date in YYYY-MM-DD format", time="Time in HH:MM format", message="What to remind you about")
async def remind(interaction: discord.Interaction, date: str, time: str, message: str):
<<<<<<< HEAD
    if not allowed(interaction):
        await interaction.response.send_message("❌ Not authorised.", ephemeral=True)
        return
=======
>>>>>>> c93f95e (several changes-updates)
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


# ─────────────────────────────────────────────────────────────────────────────
#  SLASH COMMANDS — HELP
# ─────────────────────────────────────────────────────────────────────────────

@tree.command(name="help", description="Show all available commands")
async def help_cmd(interaction: discord.Interaction):
<<<<<<< HEAD
    embed = discord.Embed(title="🤖 Bot Commands", color=discord.Color.blurple())
=======
    embed = discord.Embed(
        title="🌸 Rem's Commands",
        description="Rem is here to serve! Everything is done through commands:",
        color=discord.Color.from_rgb(150, 200, 235)
    )
    embed.add_field(name="💬 Chat & AI", value=(
        "`/ask` — Chat with Rem\n"
        "`/solve` — Upload an image for Rem to solve\n"
        "`/solve_text` — Paste text questions for Rem to solve\n"
        "`/clear_chat` — Clear Rem's conversation memory"
    ), inline=False)
>>>>>>> c93f95e (several changes-updates)
    embed.add_field(name="📂 Files", value=(
        "`/upload` — Upload a file (Notes or Assignments)\n"
        "`/getfile` — Browse & retrieve a file\n"
        "`/myfiles` — See all your uploaded files\n"
        "`/deletefile` — Delete a file by ID"
    ), inline=False)
    embed.add_field(name="📁 Categories", value=(
        "**📓 Notes:** ML, DAA, DA, DM, ESS\n"
        "**📝 Assignments:** ML Assignment, DAA Assignment, DA Assignment, DM Assignment, ESS Assignment"
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