import discord
from discord.ext import commands
import os
import asyncio
import json
import re
from flask import Flask
import threading
import time

# Render 웹 서비스용 Flask 앱 (헬스체크용)
app = Flask(__name__)

@app.route('/health')
def health_check():
    return {'status': 'ok', 'bot_user': str(bot.user) if bot.user else 'Not logged in'}

@app.route('/')
def home():
    return {'message': 'Discord Welcome Bot is running!', 'status': 'active'}

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

def load_messages():
    try:
        with open('messages.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print("❌ messages.json 파일을 찾을 수 없습니다.")
        return None

MESSAGES = load_messages()
if not MESSAGES:
    print("❌ 메시지 설정 파일을 로드할 수 없습니다.")
    exit(1)

# 봇 설정
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

processing_members = set()
member_join_history = {}
channel_creation_lock = asyncio.Lock()
creating_channels = set()
member_activity = {}

def get_clean_name(display_name):
    return re.sub(r'^\((?:단팥빵|메론빵)\)\s*', '', display_name).strip()

def has_gender_prefix(display_name):
    return bool(re.match(r'^\((?:단팥빵|메론빵)\)', display_name))

def is_rejoin(user_id, guild_id):
    key = f"{user_id}_{guild_id}"
    current_time = time.time()

    if key not in member_join_history:
        member_join_history[key] = []

    member_join_history[key] = [
        timestamp for timestamp in member_join_history[key]
        if current_time - timestamp < 86400
    ]

    is_returning = len(member_join_history[key]) > 0
    member_join_history[key].append(current_time)
    return is_returning

async def change_nickname_with_gender_prefix(member):
    try:
        if member.id == member.guild.owner_id:
            return "server_owner"

        if has_gender_prefix(member.display_name):
            return "already_has_prefix"

        male = discord.utils.get(member.guild.roles, name="남자")
        female = discord.utils.get(member.guild.roles, name="여자")

        clean_name = get_clean_name(member.display_name)

        if male and male in member.roles:
            prefix = MESSAGES['settings']['male_prefix']
            new_nickname = f"{prefix} {clean_name}"
            gender_type = "male"
        elif female and female in member.roles:
            prefix = MESSAGES['settings']['female_prefix']
            new_nickname = f"{prefix} {clean_name}"
            gender_type = "female"
        else:
            return "no_gender_role"

        if len(new_nickname) > 32:
            prefix = MESSAGES['settings'][f'{gender_type}_prefix']
            max_name_length = 32 - len(f"{prefix} ")
            truncated_name = clean_name[:max_name_length].strip()
            new_nickname = f"{prefix} {truncated_name}"

        bot_permissions = member.guild.me.guild_permissions
        if not (bot_permissions.administrator or bot_permissions.manage_nicknames):
            return "no_permission"

        if not bot_permissions.administrator:
            if member.top_role >= member.guild.me.top_role:
                return "higher_role"

        await member.edit(nick=new_nickname)
        return gender_type

    except discord.Forbidden:
        return "forbidden"
    except discord.HTTPException:
        return "http_error"
    except Exception:
        return "error"

async def grant_all_channel_access(member):
    try:
        success_count = 0
        error_count = 0

        for channel in member.guild.channels:
            if channel.category and channel.category.name == MESSAGES["settings"]["welcome_category"]:
                continue

            try:
                if isinstance(channel, discord.TextChannel):
                    await channel.set_permissions(member, read_messages=True, send_messages=True)
                    success_count += 1
                elif isinstance(channel, discord.VoiceChannel):
                    await channel.set_permissions(member, view_channel=True, connect=True)
                    success_count += 1
            except Exception as e:
                print(f"채널 접근 권한 부여 오류 ({channel.name}): {e}")
                error_count += 1

        print(f"채널 권한 부여 결과 - 성공: {success_count}, 실패: {error_count}")
        return success_count > 0

    except Exception as e:
        print(f"전체 채널 접근 권한 부여 오류: {e}")
        return False

async def notify_admin_rejoin(guild, member):
    try:
        embed = discord.Embed(
            title="🔄 재입장 알림",
            description=f"**{member.mention}** ({member.name})님이 재입장했습니다.",
            color=0xFFA500,
            timestamp=discord.utils.utcnow()
        )
        embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
    except Exception as e:
        print(f"재입장 알림 오류: {e}")

async def send_second_guide_and_activity_check(member, welcome_channel):
    try:
        if not member or not member.guild:
            return

        if not welcome_channel:
            return

        second_guide = MESSAGES["welcome_messages"]["second_guide"]
        embed = discord.Embed(
            title=second_guide["title"],
            description=second_guide["description"].format(member=member.mention),
            color=int(second_guide["color"], 16)
        )

        if "fields" in second_guide and second_guide["fields"]:
            for field in second_guide["fields"]:
                embed.add_field(
                    name=field["name"],
                    value=field["value"],
                    inline=field.get("inline", False)
                )

        if "footer" in second_guide and second_guide["footer"]:
            embed.set_footer(text=second_guide["footer"])

        view = AdaptationCheckView(member.id)
        await welcome_channel.send(embed=embed, view=view)

    except Exception as e:
        print(f"두 번째 안내문 전송 오류: {e}")

class InitialWelcomeView(discord.ui.View):
    def __init__(self, member_id):
        super().__init__(timeout=None)
        self.member_id = member_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        admin_role = discord.utils.get(interaction.guild.roles, name="ㅇㄹㅇㄹ")
        if not admin_role or admin_role not in interaction.user.roles:
            await interaction.response.send_message("❌ 관리자만 사용 가능합니다.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="삭제", style=discord.ButtonStyle.danger, emoji="❌")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ 채널 삭제 ", ephemeral=True)
        await asyncio.sleep(3)
        await interaction.channel.delete()

    @discord.ui.button(label="유지", style=discord.ButtonStyle.success, emoji="✅")
    async def preserve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅  완료.", ephemeral=True)

class AdaptationCheckView(discord.ui.View):
    def __init__(self, member_id):
        super().__init__(timeout=None)
        self.member_id = member_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("❌ 본인만 사용할 수 있습니다.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="삭제", style=discord.ButtonStyle.danger, emoji="❌")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ 채널 삭제 ", ephemeral=True)
        await asyncio.sleep(3)
        await interaction.channel.delete()

    @discord.ui.button(label="유지", style=discord.ButtonStyle.success, emoji="✅")
    async def adaptation_complete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        result = await change_nickname_with_gender_prefix(member)
        access = await grant_all_channel_access(member)

        msg = ""
        if result == "male":
            msg += "👦 단팥빵  추가!\n"
        elif result == "female":
            msg += "👧 메론빵  추가!\n"
        elif result == "already_has_prefix":
            msg += "✅ 이미 추가되어어 있음\n"
        else:
            msg += f"⚠️ 닉네임 변경 상태: {result}\n"

        msg += "✅ 완료" if access else "⚠️  실패"

        await interaction.response.send_message(msg, ephemeral=True)

@bot.event
async def on_ready():
    print(f"봇 로그인됨: {bot.user}")

@bot.event
async def on_member_join(member):
    if member.bot:
        return

    is_returning_member = is_rejoin(member.id, member.guild.id)
    if is_returning_member:
        await notify_admin_rejoin(member.guild, member)

    channel_name = f"관리자 애정듬뿍-{member.display_name}"
    unique_identifier = f"{member.id}_{member.guild.id}"

    async with channel_creation_lock:
        if unique_identifier in processing_members:
            return

        if channel_name in creating_channels:
            return

        existing_channel = discord.utils.get(member.guild.channels, name=channel_name)
        if existing_channel:
            return

        processing_members.add(unique_identifier)
        creating_channels.add(channel_name)

        try:
            guild = member.guild
            settings = MESSAGES["settings"]

            welcome_cat = discord.utils.get(guild.categories, name=settings["welcome_category"])
            if not welcome_cat:
                welcome_cat = await guild.create_category(settings["welcome_category"])

            # 애정듬뿍 채널 권한 설정 - 본인과 ㅇㄹㅇㄹ 역할만 볼 수 있음
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                member: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }

            admin_role = discord.utils.get(guild.roles, name="ㅇㄹㅇㄹ")
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            welcome_channel = await guild.create_text_channel(
                channel_name,
                category=welcome_cat,
                overwrites=overwrites
            )

            welcome_msg = MESSAGES["welcome_messages"]["first_guide"]
            embed = discord.Embed(
                title=welcome_msg["title"],
                description=welcome_msg["description"].format(member=member.mention),
                color=int(welcome_msg["color"], 16)
            )

            if "fields" in welcome_msg and welcome_msg["fields"]:
                for field in welcome_msg["fields"]:
                    embed.add_field(
                        name=field["name"],
                        value=field["value"],
                        inline=field.get("inline", False)
                    )

            if "footer" in welcome_msg and welcome_msg["footer"]:
                embed.set_footer(text=welcome_msg["footer"])

            embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)

            if is_returning_member:
                embed.add_field(
                    name="🔄 재입장 알림",
                    value="재입장.",
                    inline=False
                )

            view = InitialWelcomeView(member.id)
            await welcome_channel.send(embed=embed, view=view)

            # 추가 안내문을 별도 메시지로 전송
            additional_message = "심심해서 들어온거면 관리진들이 불러줄 때 빨리 답장하고 부르면 음챗방 오셈\n답도 안하고 활동 안할거면 **걍 딴 서버나 가라** 그런 새끼 받아주는 서버 아님 @ㅇㄹㅇㄹ"
            await welcome_channel.send(additional_message)

            await asyncio.sleep(5)
            if member in member.guild.members:
                await send_second_guide_and_activity_check(member, welcome_channel)

        finally:
            processing_members.discard(unique_identifier)
            creating_channels.discard(channel_name)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.channel.name.startswith("관리자 애정듬뿍-"):
        member_activity[message.author.id] = {
            'last_activity': time.time(),
            'channel_id': message.channel.id
        }

    await bot.process_commands(message)

flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()

TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    print("❌ DISCORD_TOKEN 환경 변수가 없습니다.")
    exit(1)

bot.run(TOKEN)
