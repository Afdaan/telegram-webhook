import asyncio
import logging
import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.request import HTTPXRequest

load_dotenv()
logger = logging.getLogger(__name__)


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name} wajib diisi")
    return value


def parse_authorized_users(value: str) -> set[int]:
    try:
        users = {int(user_id.strip()) for user_id in value.split(",") if user_id.strip()}
    except ValueError as error:
        raise RuntimeError("AUTHORIZED_USERS harus berisi ID numerik dipisahkan koma") from error

    if not users:
        raise RuntimeError("AUTHORIZED_USERS wajib memiliki minimal satu ID")
    return users

TOKEN = required_env("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = required_env("CHANNEL_ID")
AUTHORIZED_USERS = parse_authorized_users(os.getenv("AUTHORIZED_USERS", ""))

try:
    POST_LIMIT = int(os.getenv("POST_LIMIT", "50"))
except ValueError as error:
    raise RuntimeError("POST_LIMIT harus berupa angka") from error

if POST_LIMIT <= 0:
    raise RuntimeError("POST_LIMIT harus lebih besar dari 0")

user_post_count = {}
last_reset_time = time.monotonic()

# Dictionary menyimpan data postingan sementara
posts = {}
POST_STATES = {
    'WAITING_FOR_MEDIA': 'waiting_for_media',
    'WAITING_FOR_LINK': 'waiting_for_link',
    'EDITING': 'editing'
}

class PostData:
    def __init__(self, is_multiple=False):
        self.photos = []
        self.texts = []
        self.buttons_per_post = []  # List of button lists for each post
        self.state = POST_STATES['WAITING_FOR_MEDIA']
        self.is_multiple = is_multiple
        self.current_index = 0


async def authorize_update(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if user is None or user.id in AUTHORIZED_USERS:
        return

    if update.callback_query:
        await update.callback_query.answer(
            "Anda tidak memiliki akses untuk menggunakan bot ini.",
            show_alert=True,
        )
    elif update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ Anda tidak memiliki akses untuk menggunakan bot ini."
        )
    raise ApplicationHandlerStop


async def start(update: Update, context: CallbackContext) -> None:
    """Menampilkan menu utama dengan tombol."""
    keyboard = [
        [InlineKeyboardButton("📩 Single Post", callback_data="create_post_single")],
        [InlineKeyboardButton("📤 Multiple Post", callback_data="create_post_multiple")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "👋 Selamat datang di Bot AUPA!\n\nPilih menu di bawah ini untuk memulai:",
        reply_markup=reply_markup
    )

async def create_post(update: Update, context: CallbackContext) -> None:
    """Memulai pembuatan postingan baru."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    is_multiple = query.data == "create_post_multiple"
    posts[user_id] = PostData(is_multiple=is_multiple)

    message = "📸 Kirim gambar album dan teks deskripsi untuk postingan.\n\n"
    if is_multiple:
        message += ("Fitur Multiple Post:\n"
                   "1. Kirim gambar satu per satu dengan caption\n"
                   "2. Setiap gambar bisa ditambahkan button\n"
                   "3. Gunakan tombol navigasi untuk edit post sebelumnya\n"
                   "4. Semua post akan dikirim dengan button yang sama\n\n")
    message += "Ketik /cancel untuk membatalkan atau /done untuk menyelesaikan."

    await query.message.reply_text(message)

async def receive_media(update: Update, context: CallbackContext) -> None:
    """Menerima gambar + teks dari pengguna."""
    user_id = update.message.from_user.id
    if user_id not in posts:
        await update.message.reply_text("⚠️ Silakan mulai dengan /start")
        return

    post_data = posts[user_id]
    
    if not update.message.caption:
        await update.message.reply_text("⚠️ Mohon sertakan caption untuk gambar!")
        return

    post_data.photos.append(update.message.photo[-1].file_id)
    post_data.texts.append(update.message.caption)
    post_data.buttons_per_post.append([])  # Initialize empty button list for new post

    await update.message.reply_text(
        f"✅ Gambar ke-{len(post_data.photos)} diterima!"
    )
    await send_preview(update, context, user_id)
    
    if post_data.is_multiple:
        await update.message.reply_text(
            "Multiple Post Mode:\n"
            "1. Edit post ini (tambah button jika diperlukan)\n"
            "2. Klik 'Next ➡️' untuk lanjut ke post berikutnya\n"
            "3. Kirim gambar lain untuk menambah post baru\n"
            "4. Di post terakhir, klik '✅ Done' untuk mengirim semua"
        )

async def add_link(update: Update, context: CallbackContext) -> None:
    """Memulai proses penambahan tombol link."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in posts:
        await send_message(update, "⚠️ Sesi telah berakhir. Silakan mulai dengan /start", context=context)
        return

    post_data = posts[user_id]
    current_index = post_data.current_index
    post_data.state = POST_STATES['WAITING_FOR_LINK']

    # Tampilkan status current buttons
    current_buttons = post_data.buttons_per_post[current_index]
    status_text = f"📝 Menambah button untuk Post {current_index + 1}"
    if post_data.is_multiple:
        status_text += f"/{len(post_data.photos)}"
    
    if current_buttons:
        status_text += "\n\nButton yang sudah ada:"
        for i, btn in enumerate(current_buttons, 1):
            status_text += f"\n{i}. {btn.text} - {btn.url}"
    
    instructions = (
        "\n\n✏️ Kirim button baru dengan format:\n"
        "Nama Button - URL\n\n"
        "Contoh:\n"
        "🎵 Spotify - https://spotify.com/...\n"
        "📥 Download - https://download.com/...\n\n"
        "• Bisa kirim beberapa sekaligus (satu baris satu button)\n"
        "• Ketik /done untuk selesai dan melihat preview"
    )
    
    await send_message(update, status_text + instructions, context=context)

async def send_preview(update: Update, context: CallbackContext, user_id: int):
    """Mengirim preview postingan dengan semua tombol yang sudah dibuat."""
    if user_id not in posts:
        return

    post_data = posts[user_id]
    current_index = post_data.current_index

    if not post_data.photos:
        return

    # Get chat_id from either callback_query or message
    chat_id = None
    if hasattr(update, 'callback_query') and update.callback_query and update.callback_query.message:
        chat_id = update.callback_query.message.chat_id
    elif hasattr(update, 'message') and update.message:
        chat_id = update.message.chat_id
    else:
        logger.warning("Tidak dapat mengirim preview tanpa chat")
        return

    # Tampilkan button untuk post yang sedang aktif
    reply_markup = None
    current_buttons = post_data.buttons_per_post[current_index]
    if current_buttons:
        reply_markup = InlineKeyboardMarkup([[button] for button in current_buttons])

    # Navigation buttons for multiple posts
    preview_keyboard = []
    if post_data.is_multiple:
        nav_buttons = []
        if current_index > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data="prev_preview"))
        if current_index < len(post_data.photos) - 1:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data="next_preview"))
        if nav_buttons:
            preview_keyboard.append(nav_buttons)

    # Edit buttons
    preview_keyboard.extend([
        [InlineKeyboardButton("➕ Add Linkbutton", callback_data="add_link")],
        [InlineKeyboardButton("🗑 Delete Linkbutton", callback_data="delete_link")],
        [InlineKeyboardButton("✅ Done", callback_data="done")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

    interface_markup = InlineKeyboardMarkup(preview_keyboard)

    # Send preview with current index and button info
    current_buttons = post_data.buttons_per_post[current_index]
    preview_text = f"{post_data.texts[current_index]}"
    
    if post_data.is_multiple:
        next_text = f"\n\n📑 Post {current_index + 1}/{len(post_data.photos)}"
        
        # Tampilkan info button
        if current_buttons:
            next_text += f"\n\n🔘 Button pada post ini:"
            for i, btn in enumerate(current_buttons, 1):
                next_text += f"\n{i}. {btn.text}"
        
        # Tampilkan instruksi navigasi
        if current_index < len(post_data.photos) - 1:
            next_text += "\n\n➡️ Klik 'Next' untuk melanjutkan ke post berikutnya"
        else:
            next_text += "\n\n✅ Ini post terakhir, klik 'Done' untuk mengirim semua"
        
        preview_text += next_text
    
    # Send photo preview using context.bot
    try:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=post_data.photos[current_index],
            caption=preview_text,
            reply_markup=reply_markup
        )
    except Exception:
        logger.exception("Gagal mengirim foto preview ke chat %s", chat_id)
        # Fallback jika gagal mengirim preview dengan photo
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"📸 [Preview Post {current_index + 1}]\n\n{preview_text}",
                reply_markup=reply_markup
            )
        except Exception:
            logger.exception("Fallback preview gagal untuk chat %s", chat_id)
            return
    
    # Customize edit message based on multiple post state
    edit_message = "🔧 Edit postingan:"
    if post_data.is_multiple:
        edit_message = f"🔧 Edit Post {current_index + 1}/{len(post_data.photos)}:"
        if current_index < len(post_data.photos) - 1:
            edit_message += "\nSetelah selesai edit, klik 'Next ➡️' untuk lanjut ke post berikutnya" 
        else:
            edit_message += "\nIni post terakhir, klik '✅ Done' untuk mengirim semua post"
    
    # Send edit interface using context.bot
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=edit_message,
            reply_markup=interface_markup
        )
    except Exception:
        logger.exception("Gagal mengirim interface edit ke chat %s", chat_id)
    
    post_data.state = POST_STATES['EDITING']

async def navigate_preview(update: Update, context: CallbackContext) -> None:
    """Handle navigation between multiple posts in preview."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in posts:
        await send_message(update, "⚠️ Sesi telah berakhir. Silakan mulai dengan /start", context=context)
        return

    post_data = posts[user_id]
    
    # Handle navigation (next or prev)
    if query.data == "next_preview" and post_data.current_index < len(post_data.photos) - 1:
        post_data.current_index += 1
    elif query.data == "prev_preview" and post_data.current_index > 0:
        post_data.current_index -= 1
    
    # Reset state ke EDITING untuk tampilan baru
    post_data.state = POST_STATES['EDITING']
    
    # Tampilkan status post berikutnya
    status = f"📝 Post {post_data.current_index + 1}/{len(post_data.photos)}"
    current_buttons = post_data.buttons_per_post[post_data.current_index]
    
    if current_buttons:
        status += "\n\n🔘 Button yang ada di post ini:"
        for i, btn in enumerate(current_buttons, 1):
            status += f"\n{i}. {btn.text}"
    
    # Tampilkan opsi untuk navigasi
    keyboard = []
    nav_buttons = []
    
    if post_data.current_index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Post Sebelumnya", callback_data="prev_preview"))
    if post_data.current_index < len(post_data.photos) - 1:
        nav_buttons.append(InlineKeyboardButton("Post Berikutnya ➡️", callback_data="next_preview"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
        
    keyboard.extend([
        [InlineKeyboardButton("➕ Add Button", callback_data="add_link")],
        [InlineKeyboardButton("🔍 Preview Post", callback_data="back_to_preview")],
        [InlineKeyboardButton("✅ Done", callback_data="done")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])
    markup = InlineKeyboardMarkup(keyboard)
    
    # Tambahkan informasi navigasi
    if post_data.current_index == 0:
        status += "\n\n🔸 Ini adalah post pertama"
    elif post_data.current_index == len(post_data.photos) - 1:
        status += "\n\n🔸 Ini adalah post terakhir"
    else:
        status += f"\n\n🔸 Post {post_data.current_index + 1} dari {len(post_data.photos)}"
    
    await send_message(update, status, reply_markup=markup, context=context)
    await send_preview(update, context, user_id)

async def ping(update: Update, context: CallbackContext) -> None:
    """Menampilkan status dan latensi bot."""
    start_time = time.monotonic()
    message = await update.message.reply_text("🏓 Mengukur latensi...")
    end_time = time.monotonic()
    latency = round((end_time - start_time) * 1000)
    await message.edit_text(f"🏓 Pong!\n🤖 Bot aktif dan berjalan dengan baik.\n⚡ Latensi: {latency}ms")

async def cancel_command(update: Update, context: CallbackContext) -> None:
    """Handle /cancel command"""
    user_id = update.message.from_user.id
    if user_id in posts:
        del posts[user_id]
        await update.message.reply_text("❌ Postingan dibatalkan.")
    else:
        await update.message.reply_text("⚠️ Tidak ada postingan aktif untuk dibatalkan.")


def has_post_capacity(user_id: int, requested_posts: int) -> bool:
    global last_reset_time

    now = time.monotonic()
    if now - last_reset_time >= 60:
        user_post_count.clear()
        last_reset_time = now

    return user_post_count.get(user_id, 0) + requested_posts <= POST_LIMIT


async def publish_posts(update: Update, context: CallbackContext, user_id: int) -> None:
    post_data = posts.get(user_id)
    if post_data is None:
        await send_message(
            update,
            "⚠️ Tidak ada postingan aktif. Silakan mulai dengan /start",
            context=context,
        )
        return

    total_posts = len(post_data.photos)
    if not post_data.photos or not post_data.texts:
        await send_message(update, "⚠️ Tidak ada postingan yang bisa dikirim!", context=context)
        return

    if not (
        len(post_data.photos)
        == len(post_data.texts)
        == len(post_data.buttons_per_post)
    ):
        logger.error("Data post tidak konsisten untuk user %s", user_id)
        await send_message(
            update,
            "⚠️ Data postingan tidak lengkap. Batalkan lalu buat postingan baru.",
            context=context,
        )
        return

    if not has_post_capacity(user_id, total_posts):
        await send_message(
            update,
            f"⚠️ Pengiriman ini melewati batas {POST_LIMIT} postingan per menit.",
            context=context,
        )
        return

    success_count = 0
    failed_posts = []
    for index, (photo, text, buttons) in enumerate(
        zip(post_data.photos, post_data.texts, post_data.buttons_per_post),
        1,
    ):
        reply_markup = InlineKeyboardMarkup([[button] for button in buttons]) if buttons else None
        try:
            await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=photo,
                caption=text,
                reply_markup=reply_markup,
            )
        except Exception:
            failed_posts.append(index)
            logger.exception("Gagal mengirim post %s untuk user %s", index, user_id)
        else:
            success_count += 1
            user_post_count[user_id] = user_post_count.get(user_id, 0) + 1

        if index < total_posts:
            await asyncio.sleep(0.5)

    if success_count == 0:
        await send_message(update, "❌ Gagal mengirim semua postingan. Coba lagi nanti.", context=context)
        return

    posts.pop(user_id, None)
    if failed_posts:
        failed_list = ", ".join(map(str, failed_posts))
        await send_message(
            update,
            f"⚠️ Berhasil mengirim {success_count} dari {total_posts} postingan. "
            f"Post gagal: {failed_list}.",
            context=context,
        )
        return

    await send_message(
        update,
        f"✅ Berhasil mengirim {success_count} postingan ke channel!",
        context=context,
    )


async def done_command(update: Update, context: CallbackContext) -> None:
    """Handle /done command"""
    await publish_posts(update, context, update.message.from_user.id)

async def cancel(update: Update, context: CallbackContext) -> None:
    """Membatalkan postingan."""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        if user_id in posts:
            del posts[user_id]
            await send_message(update, "❌ Postingan dibatalkan.", context=context)
    else:
        await cancel_command(update, context)

async def done(update: Update, context: CallbackContext) -> None:
    """Mengirim postingan ke channel."""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await publish_posts(update, context, query.from_user.id)
    else:
        await done_command(update, context)

async def delete_link(update: Update, context: CallbackContext) -> None:
    """Menghapus tombol terakhir yang ditambahkan."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in posts:
        await send_message(update, "⚠️ Sesi telah berakhir. Silakan mulai dengan /start", context=context)
        return

    post_data = posts[user_id]
    current_buttons = post_data.buttons_per_post[post_data.current_index]
    if current_buttons:
        removed_button = current_buttons.pop()
        await send_message(update, f"✅ Tombol '{removed_button.text}' dihapus dari post {post_data.current_index + 1}!", context=context)
        await send_preview(update, context, user_id)
    else:
        await send_message(update, "⚠️ Tidak ada tombol yang bisa dihapus untuk post ini!", context=context)

async def receive_link(update: Update, context: CallbackContext) -> None:
    """Menerima link dari pengguna dan menambahkannya ke postingan."""
    user_id = update.message.from_user.id
    if user_id not in posts:
        await update.message.reply_text("⚠️ Sesi telah berakhir. Silakan mulai dengan /start")
        return

    post_data = posts[user_id]
    if post_data.state != POST_STATES['WAITING_FOR_LINK']:
        return

    text = update.message.text
    if text.lower() == '/done':
        post_data.state = POST_STATES['EDITING']
        await update.message.reply_text("✅ Selesai menambahkan button!")
        await send_preview(update, context, user_id)
        if post_data.is_multiple and post_data.current_index < len(post_data.photos) - 1:
            await update.message.reply_text(
                "📝 Klik 'Next ➡️' untuk melanjutkan ke post berikutnya, atau tambahkan button lagi jika diperlukan."
            )
        return

    # Validasi format button
    if "-" not in text:
        await update.message.reply_text(
            "❌ Format salah!\n\n"
            "Format yang benar:\n"
            "Nama Button - https://url.com\n\n"
            "Contoh:\n"
            "🎵 Spotify - https://spotify.com/...\n"
            "📥 Download - https://download.com/..."
        )
        return

    lines = text.split("\n")
    added_buttons = 0
    invalid_buttons = []

    for line in lines:
        if not line.strip():  # Skip empty lines
            continue
            
        button_data = line.split("-", 1)
        if len(button_data) == 2:
            button_name = button_data[0].strip()
            button_url = button_data[1].strip()

            if not button_name:
                invalid_buttons.append(f"❌ Nama button tidak boleh kosong: {line}")
                continue

            if not button_url.startswith("http"):
                invalid_buttons.append(f"❌ URL harus dimulai dengan http: {button_url}")
                continue

            # Add button to current post
            post_data.buttons_per_post[post_data.current_index].append(
                InlineKeyboardButton(button_name, url=button_url)
            )
            added_buttons += 1

    # Beri feedback
    if added_buttons > 0:
        # Tampilkan info button yang berhasil ditambahkan
        success_msg = f"✅ Berhasil menambahkan {added_buttons} button ke Post {post_data.current_index + 1}"
        if post_data.is_multiple:
            success_msg += f"/{len(post_data.photos)}"
        
        # Tampilkan semua button yang ada di post ini
        current_buttons = post_data.buttons_per_post[post_data.current_index]
        success_msg += "\n\n🔘 Button pada post ini:"
        for i, btn in enumerate(current_buttons, 1):
            success_msg += f"\n{i}. {btn.text} - {btn.url}"
            
        # Buat keyboard untuk navigasi
        keyboard = [
            [InlineKeyboardButton("🔍 Preview Post", callback_data="back_to_preview")]
        ]
        
        # Tambahkan opsi Next jika multiple post dan bukan post terakhir
        if post_data.is_multiple and post_data.current_index < len(post_data.photos) - 1:
            keyboard.append([InlineKeyboardButton("➡️ Next Post", callback_data="next_preview")])
            
        # Selalu tampilkan opsi Add Button
        keyboard.append([InlineKeyboardButton("➕ Add Button", callback_data="add_link")])
        
        markup = InlineKeyboardMarkup(keyboard)
        
        # Pesan navigasi yang lebih jelas
        success_msg += "\n\n📝 Langkah selanjutnya:"
        success_msg += "\n1. Preview Post - Lihat hasil dengan button"
        if post_data.is_multiple and post_data.current_index < len(post_data.photos) - 1:
            success_msg += "\n2. Next Post - Lanjut ke post berikutnya"
        success_msg += f"\n{'3' if post_data.is_multiple and post_data.current_index < len(post_data.photos) - 1 else '2'}. Add Button - Tambah button lagi untuk post ini"
        
        await update.message.reply_text(success_msg, reply_markup=markup)

    # Tampilkan error jika ada
    if invalid_buttons:
        error_msg = "⚠️ Beberapa button tidak valid:\n" + "\n".join(invalid_buttons)
        await update.message.reply_text(error_msg)

async def back_to_preview(update: Update, context: CallbackContext) -> None:
    """Handler untuk kembali ke preview setelah menambah button"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in posts:
        await send_message(update, "⚠️ Sesi telah berakhir. Silakan mulai dengan /start", context=context)
        return
        
    post_data = posts[user_id]
    # Reset state ke EDITING untuk menampilkan preview
    post_data.state = POST_STATES['EDITING']
    
    # Tampilkan ringkasan button yang sudah ditambahkan
    current_buttons = post_data.buttons_per_post[post_data.current_index]
    if current_buttons:
        summary = f"🔘 Button yang sudah ditambahkan untuk Post {post_data.current_index + 1}:"
        for i, btn in enumerate(current_buttons, 1):
            summary += f"\n{i}. {btn.text}"
        await send_message(update, summary, context=context)
    
    await send_preview(update, context, user_id)

async def error_handler(update: object, context: CallbackContext) -> None:
    """Menangani error agar bot tidak crash."""
    error = context.error
    if error is None:
        logger.error("Error handler dipanggil tanpa exception")
    else:
        logger.error(
            "Error saat memproses update",
            exc_info=(type(error), error, error.__traceback__),
        )
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Terjadi kesalahan, coba lagi nanti.")
        except Exception:
            logger.exception("Gagal mengirim pesan error ke pengguna")


async def send_message(update: Update, text: str, reply_markup=None, context=None):
    """Helper function to send messages regardless of update source (message or callback)."""
    chat = update.effective_chat
    if chat is None or context is None:
        logger.warning("Tidak dapat mengirim pesan tanpa chat atau context")
        return

    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=text,
            reply_markup=reply_markup,
        )
    except Exception:
        logger.exception("Gagal mengirim pesan ke chat %s", chat.id)


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ('/health', '/'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def create_health_server() -> HTTPServer:
    try:
        port = int(os.getenv("PORT", "8080"))
    except ValueError as error:
        raise RuntimeError("PORT harus berupa angka") from error

    if not 1 <= port <= 65535:
        raise RuntimeError("PORT harus berada antara 1 dan 65535")

    return HTTPServer(("0.0.0.0", port), HealthCheckHandler)


def build_application() -> Application:
    request_config = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=20.0,
        write_timeout=20.0,
        httpx_kwargs={
            "transport": httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        },
    )
    application = Application.builder().token(TOKEN).request(request_config).build()
    application.add_handler(TypeHandler(Update, authorize_update), group=-1)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CallbackQueryHandler(create_post, pattern="^create_post_"))
    application.add_handler(CallbackQueryHandler(back_to_preview, pattern="^back_to_preview$"))
    application.add_handler(CallbackQueryHandler(navigate_preview, pattern="^next_preview$"))
    application.add_handler(CallbackQueryHandler(navigate_preview, pattern="^prev_preview$"))
    application.add_handler(CallbackQueryHandler(add_link, pattern="^add_link$"))
    application.add_handler(CallbackQueryHandler(delete_link, pattern="^delete_link$"))
    application.add_handler(CallbackQueryHandler(done, pattern="^done$"))
    application.add_handler(CallbackQueryHandler(cancel, pattern="^cancel$"))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("done", done_command))
    application.add_handler(MessageHandler(filters.PHOTO & filters.CAPTION, receive_media))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    application = build_application()
    health_server = create_health_server()
    health_thread = threading.Thread(target=health_server.serve_forever, daemon=True)
    health_thread.start()
    logger.info("Health check aktif pada port %s", health_server.server_port)
    logger.info("Bot AUPA berjalan dalam mode polling")

    try:
        application.run_polling(drop_pending_updates=True)
    finally:
        health_server.shutdown()
        health_server.server_close()
        health_thread.join(timeout=5)


if __name__ == "__main__":
    main()
