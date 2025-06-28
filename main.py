from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, CallbackContext
import time
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Token bot dan ID channel
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

# Batas akses pengguna
AUTHORIZED_USERS = set(map(int, os.getenv("AUTHORIZED_USERS", "").split(",")))

# Anti-spam (batas 50 post per menit)
POST_LIMIT = int(os.getenv("POST_LIMIT", "50"))
user_post_count = {}
last_reset_time = time.time()

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
        self.buttons = []
        self.state = POST_STATES['WAITING_FOR_MEDIA']
        self.is_multiple = is_multiple
        self.current_index = 0

async def start(update: Update, context: CallbackContext) -> None:
    """Menampilkan menu utama dengan tombol."""
    if update.message.from_user.id not in AUTHORIZED_USERS:
        await update.message.reply_text("⚠️ Anda tidak memiliki akses untuk menggunakan bot ini.")
        return

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

    message = "📸 Kirim gambar album dan teks deskripsi untuk postingan."
    if is_multiple:
        message += "\nAnda dapat mengirim beberapa gambar secara berurutan."
    message += "\nKetik /cancel untuk membatalkan atau /done untuk menyelesaikan."

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

    if post_data.is_multiple:
        await update.message.reply_text(
            f"✅ Gambar ke-{len(post_data.photos)} diterima!\n"
            "Kirim gambar lain atau ketik /done untuk menyelesaikan."
        )
    else:
        await send_preview(update, context, user_id)

async def add_link(update: Update, context: CallbackContext) -> None:
    """Memulai proses penambahan tombol link."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in posts:
        await query.message.reply_text("⚠️ Sesi telah berakhir. Silakan mulai dengan /start")
        return

    posts[user_id].state = POST_STATES['WAITING_FOR_LINK']
    await query.message.reply_text(
        "📝 Kirim link dengan format:\nNama Button - URL\n\n"
        "Kamu bisa mengirim beberapa link sekaligus (satu per baris).\n"
        "Ketik /done untuk selesai menambahkan link."
    )

async def send_preview(update: Update, context: CallbackContext, user_id: int):
    """Mengirim preview postingan dengan semua tombol yang sudah dibuat."""
    if user_id not in posts:
        return

    post_data = posts[user_id]
    current_index = post_data.current_index

    if not post_data.photos:
        return

    # Jika tidak ada tombol, buat default reply_markup None
    reply_markup = None
    if post_data.buttons:
        reply_markup = InlineKeyboardMarkup([[button] for button in post_data.buttons])

    # Navigation buttons for multiple posts
    preview_keyboard = []
    if post_data.is_multiple and len(post_data.photos) > 1:
        nav_buttons = []
        if current_index > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data="prev_preview"))
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

    # Send preview with current index
    await update.message.reply_photo(
        photo=post_data.photos[current_index],
        caption=f"{post_data.texts[current_index]}\n\n{'🔄 Preview ' + str(current_index + 1) + '/' + str(len(post_data.photos)) if post_data.is_multiple else ''}",
        reply_markup=reply_markup
    )
    await update.message.reply_text("🔧 Edit postingan:", reply_markup=interface_markup)
    post_data.state = POST_STATES['EDITING']

async def navigate_preview(update: Update, context: CallbackContext) -> None:
    """Handle navigation between multiple posts in preview."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in posts:
        return

    post_data = posts[user_id]
    if query.data == "next_preview" and post_data.current_index < len(post_data.photos) - 1:
        post_data.current_index += 1
    elif query.data == "prev_preview" and post_data.current_index > 0:
        post_data.current_index -= 1

    await send_preview(update, context, user_id)

async def cancel(update: Update, context: CallbackContext) -> None:
    """Membatalkan postingan."""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if user_id in posts:
        del posts[user_id]
        await query.message.reply_text("❌ Postingan dibatalkan.")

async def delete_link(update: Update, context: CallbackContext) -> None:
    """Menghapus tombol terakhir yang ditambahkan."""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if user_id in posts and posts[user_id]["buttons"]:
        removed_button = posts[user_id]["buttons"].pop()
        await query.message.reply_text(f"✅ Tombol '{removed_button.text}' dihapus!")

        # Kirim ulang preview setelah penghapusan tombol
        await send_preview(update, context, user_id)

async def done(update: Update, context: CallbackContext) -> None:
    """Mengirim postingan ke channel."""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if user_id not in posts:
        await query.message.reply_text("⚠️ Sesi telah berakhir. Silakan mulai dengan /start")
        return

    post_data = posts[user_id]

    # Cek batasan postingan untuk anti-spam
    global last_reset_time
    if time.time() - last_reset_time >= 60:
        user_post_count.clear()
        last_reset_time = time.time()

    if user_id not in user_post_count:
        user_post_count[user_id] = 0

    if user_post_count[user_id] >= POST_LIMIT:
        await query.message.reply_text("⚠️ Anda mencapai batas 50 postingan per menit.")
        return

    if not post_data.photos or not post_data.texts:
        await query.message.reply_text("⚠️ Tidak ada postingan yang bisa dikirim!")
        return

    # Setup reply markup if buttons exist
    reply_markup = None
    if post_data.buttons:
        reply_markup = InlineKeyboardMarkup([[button] for button in post_data.buttons])

    try:
        # Send all posts
        for photo, text in zip(post_data.photos, post_data.texts):
            await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=photo,
                caption=text,
                reply_markup=reply_markup
            )
            user_post_count[user_id] += 1

        await query.message.reply_text(
            f"✅ Berhasil mengirim {len(post_data.photos)} postingan ke channel!"
        )
        del posts[user_id]
    except Exception as e:
        await query.message.reply_text(f"⚠️ Terjadi kesalahan saat mengirim postingan: {str(e)}")

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
    if text == '/done':
        post_data.state = POST_STATES['EDITING']
        await send_preview(update, context, user_id)
        return

    lines = text.split("\n")
    added_buttons = 0
    for line in lines:
        if "-" in line:
            button_data = line.split("-", 1)
            if len(button_data) == 2:
                button_name = button_data[0].strip()
                button_url = button_data[1].strip()

                if button_url.startswith("http"):
                    post_data.buttons.append(
                        InlineKeyboardButton(button_name, url=button_url)
                    )
                    added_buttons += 1
                else:
                    await update.message.reply_text(
                        f"⚠️ URL tidak valid: {button_url}. Pastikan dimulai dengan 'http'"
                    )

    if added_buttons > 0:
        await update.message.reply_text(
            f"✅ Berhasil menambahkan {added_buttons} tombol!\n"
            "Kirim link lain atau ketik /done untuk selesai."
        )

# Handler error agar bot tidak crash
async def error_handler(update: object, context: CallbackContext) -> None:
    """Menangani error agar bot tidak crash."""
    print(f"Error terjadi: {context.error}")
    if update and isinstance(update, Update) and update.message:
        await update.message.reply_text("⚠️ Terjadi kesalahan, coba lagi nanti.")

# Setup bot
app = Application.builder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(create_post, pattern="^create_post_"))
app.add_handler(CallbackQueryHandler(add_link, pattern="add_link"))
app.add_handler(CallbackQueryHandler(delete_link, pattern="delete_link"))
app.add_handler(CallbackQueryHandler(navigate_preview, pattern="^(next|prev)_preview$"))
app.add_handler(CallbackQueryHandler(cancel, pattern="cancel"))
app.add_handler(CallbackQueryHandler(done, pattern="done"))
app.add_handler(CommandHandler("cancel", cancel))
app.add_handler(CommandHandler("done", done))
app.add_handler(MessageHandler(filters.PHOTO & filters.CAPTION, receive_media))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link))
app.add_error_handler(error_handler)

# Run bot
print("🤖 Bot AUPA berjalan...")
app.run_polling()
