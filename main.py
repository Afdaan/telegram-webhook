from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, CallbackContext
import time
import os
import asyncio
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
        self.buttons_per_post = []  # List of button lists for each post
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
        print("Warning: No valid chat_id found in send_preview")
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
    except Exception as e:
        print(f"Error sending photo preview: {e}")
        # Fallback jika gagal mengirim preview dengan photo
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"📸 [Preview Post {current_index + 1}]\n\n{preview_text}",
                reply_markup=reply_markup
            )
        except Exception as fallback_error:
            print(f"Fallback error: {fallback_error}")
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
    except Exception as e:
        print(f"Error sending edit interface: {e}")
    
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

async def cancel_command(update: Update, context: CallbackContext) -> None:
    """Handle /cancel command"""
    user_id = update.message.from_user.id
    if user_id in posts:
        del posts[user_id]
        await update.message.reply_text("❌ Postingan dibatalkan.")
    else:
        await update.message.reply_text("⚠️ Tidak ada postingan aktif untuk dibatalkan.")

async def done_command(update: Update, context: CallbackContext) -> None:
    """Handle /done command"""
    user_id = update.message.from_user.id
    
    if user_id not in posts:
        await update.message.reply_text("⚠️ Tidak ada postingan aktif. Silakan mulai dengan /start")
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
        await update.message.reply_text("⚠️ Anda mencapai batas 50 postingan per menit.")
        return

    if not post_data.photos or not post_data.texts:
        await update.message.reply_text("⚠️ Tidak ada postingan yang bisa dikirim!")
        return

    # Send all posts with their respective buttons
    try:
        success_count = 0
        for i, (photo, text, buttons) in enumerate(zip(post_data.photos, post_data.texts, post_data.buttons_per_post), 1):
            # Setup reply markup for this specific post
            reply_markup = None
            if buttons:
                reply_markup = InlineKeyboardMarkup([[button] for button in buttons])
                
            await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=photo,
                caption=text,
                reply_markup=reply_markup
            )
            user_post_count[user_id] += 1

        await update.message.reply_text(
            f"✅ Berhasil mengirim {len(post_data.photos)} postingan ke channel!"
        )
        del posts[user_id]
    except Exception as e:
        await update.message.reply_text(f"⚠️ Terjadi kesalahan saat mengirim postingan: {str(e)}")

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
        user_id = query.from_user.id
        if user_id not in posts:
            await send_message(update, "⚠️ Sesi telah berakhir. Silakan mulai dengan /start", context=context)
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
            await send_message(update, "⚠️ Anda mencapai batas 50 postingan per menit.", context=context)
            return

        if not post_data.photos or not post_data.texts:
            await send_message(update, "⚠️ Tidak ada postingan yang bisa dikirim!", context=context)
            return

        # Send all posts with their respective buttons
        success_count = 0
        for i, (photo, text, buttons) in enumerate(zip(post_data.photos, post_data.texts, post_data.buttons_per_post), 1):
            # Setup reply markup for this specific post
            reply_markup = None
            if buttons:
                reply_markup = InlineKeyboardMarkup([[button] for button in buttons])
            try:
                await context.bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=photo,
                    caption=text,
                    reply_markup=reply_markup
                )
                success_count += 1
                user_post_count[user_id] += 1
                if i < len(post_data.photos):  # Don't sleep after last post
                    await asyncio.sleep(0.5)  # Delay kecil antar pengiriman
            except Exception as send_error:
                await send_message(update, f"⚠️ Gagal mengirim post ke-{i}: {str(send_error)}", context=context)
                continue

        if success_count > 0:
            total_posts = len(post_data.photos)
            if success_count == total_posts:
                await send_message(update, "✅ Semua postingan berhasil dikirim ke channel!", context=context)
            else:
                await send_message(update, 
                    f"⚠️ Berhasil mengirim {success_count} dari {total_posts} postingan ke channel.",
                    context=context
                )
            del posts[user_id]
        else:
            await send_message(update, "❌ Gagal mengirim semua postingan!", context=context)
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

# Handler error agar bot tidak crash
async def error_handler(update: object, context: CallbackContext) -> None:
    """Menangani error agar bot tidak crash."""
    print(f"Error terjadi: {context.error}")
    if update and isinstance(update, Update) and update.message:
        await update.message.reply_text("⚠️ Terjadi kesalahan, coba lagi nanti.")

async def send_message(update: Update, text: str, reply_markup=None, context=None):
    """Helper function to send messages regardless of update source (message or callback)."""
    try:
        # Get chat_id from either callback_query or message
        chat_id = None
        if hasattr(update, 'callback_query') and update.callback_query and update.callback_query.message:
            chat_id = update.callback_query.message.chat_id
        elif hasattr(update, 'message') and update.message:
            chat_id = update.message.chat_id
        
        if not chat_id:
            print("Warning: No valid chat_id found in send_message")
            return
            
        # If we have context, use context.bot.send_message (most reliable)
        if context:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup
            )
        # For messages without context, use reply_text (safe)
        elif hasattr(update, 'message') and update.message and not hasattr(update, 'callback_query'):
            await update.message.reply_text(text, reply_markup=reply_markup)
        # For callback queries without context, use callback_query.message.reply_text
        elif hasattr(update, 'callback_query') and update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
        else:
            print("Error: Cannot send message - no valid method found")
                
    except Exception as e:
        print(f"Error sending message: {e}")

# Setup bot
app = Application.builder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(create_post, pattern="^create_post_"))
# Urutkan handler berdasarkan prioritas
app.add_handler(CallbackQueryHandler(back_to_preview, pattern="^back_to_preview$"))
app.add_handler(CallbackQueryHandler(navigate_preview, pattern="^next_preview$"))
app.add_handler(CallbackQueryHandler(navigate_preview, pattern="^prev_preview$"))
app.add_handler(CallbackQueryHandler(add_link, pattern="^add_link$"))
app.add_handler(CallbackQueryHandler(delete_link, pattern="^delete_link$"))
app.add_handler(CallbackQueryHandler(done, pattern="^done$"))
app.add_handler(CallbackQueryHandler(cancel, pattern="^cancel$"))
app.add_handler(CommandHandler("cancel", cancel_command))
app.add_handler(CommandHandler("done", done_command))
app.add_handler(MessageHandler(filters.PHOTO & filters.CAPTION, receive_media))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link))
app.add_error_handler(error_handler)

# Run bot
print("🤖 Bot AUPA berjalan...")
app.run_polling()
