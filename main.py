import discord
from discord.ext import commands
import os
import asyncio
import json
import re
from flask import Flask
import threading

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

def get_clean_name(display_name):
    return re.sub(r'^\((?:단팥빵|메론빵)\)\s*', '', display_name).strip()

def has_gender_prefix(display_name):
    return bool(re.match(r'^\((?:단팥빵|메론빵)\)', display_name))

async def change_nickname_with_gender_prefix(member):
    try:
        if has_gender_prefix(member.display_name):
            return "already_has_prefix"
        male = discord.utils.get(member.guild.roles, name=MESSAGES["settings"]["male_role_name"])
        female = discord.utils.get(member.guild.roles, name=MESSAGES["settings"]["female_role_name"])
        name = get_clean_name(member.display_name)
        if male in member.roles:
            await member.edit(nick=f"{MESSAGES['settings']['male_prefix']} {name}")
            return "male"
        elif female in member.roles:
            await member.edit(nick=f"{MESSAGES['settings']['female_prefix']} {name}")
            return "female"
        return "no_gender_role"
    except Exception as e:
        print(f"닉네임 변경 오류: {e}")
        return "error"

async def grant_all_channel_access(member):
    try:
        for channel in member.guild.channels:
            if channel.category and channel.category.name == MESSAGES["settings"]["welcome_category"]:
                continue
            if isinstance(channel, discord.TextChannel):
                await channel.set_permissions(member, read_messages=True, send_messages=True)
            elif isinstance(channel, discord.VoiceChannel):
                await channel.set_permissions(member, view_channel=True, connect=True)
        return True
    except Exception as e:
        print(f"채널 접근 권한 부여 오류: {e}")
        return False

class InitialWelcomeView(discord.ui.View):
    def __init__(self, member_id):
        super().__init__(timeout=None)
        self.member_id = member_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        doradori_role = discord.utils.get(interaction.guild.roles, name="도라도라미")
        if not doradori_role or doradori_role not in interaction.user.roles:
            await interaction.response.send_message("❌ 도라도라미 역할이 있는 사람만 사용할 수 있습니다.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="삭제", style=discord.ButtonStyle.danger, emoji="❌")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ 채널 삭제 요청됨", ephemeral=True)
        await asyncio.sleep(3)
        try:
            await interaction.channel.delete()
        except:
            pass

    @discord.ui.button(label="보존", style=discord.ButtonStyle.success, emoji="✅")
    async def preserve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        member_name = interaction.channel.name.replace("환영-", "")
        member = discord.utils.get(interaction.guild.members, name=member_name)
        if not member:
            await interaction.response.send_message("❌ 해당 멤버를 찾을 수 없습니다.", ephemeral=True)
            return
        
        await grant_all_channel_access(member)
        await interaction.response.send_message("✅ 모든 채널 접근 권한이 부여되었습니다. 환영 채널이 보존되었습니다.", ephemeral=True)

class AdaptationCheckView(discord.ui.View):
    def __init__(self, member_id):
        super().__init__(timeout=None)
        self.member_id = member_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # 본인만 버튼을 누를 수 있도록 체크
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("❌ 본인만 이 버튼을 사용할 수 있습니다.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="삭제", style=discord.ButtonStyle.danger, emoji="❌")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ 채널 삭제 요청됨", ephemeral=True)
        await asyncio.sleep(3)
        try:
            await interaction.channel.delete()
        except:
            pass

    @discord.ui.button(label="보존", style=discord.ButtonStyle.success, emoji="✅")
    async def preserve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user  # 본인이 버튼을 누르므로 interaction.user 사용
        
        result = await change_nickname_with_gender_prefix(member)
        access = await grant_all_channel_access(member)

        msg = ""
        if result == "male":
            msg += f"👦 {get_clean_name(member.display_name)} 님의 닉네임에 단팥빵 접두사가 추가되었습니다.\n"
        elif result == "female":
            msg += f"👧 {get_clean_name(member.display_name)} 님의 닉네임에 메론빵 접두사가 추가되었습니다.\n"
        elif result == "already_has_prefix":
            msg += "이미 접두사가 포함된 닉네임입니다.\n"
        elif result == "no_gender_role":
            msg += "⚠️ 성별 역할(단팥빵/메론빵)이 없어서 닉네임 변경을 건너뜁니다.\n"
        else:
            msg += f"❌ 닉네임 변경에 실패했습니다.\n"

        if access:
            msg += "✅ 모든 채널 접근 권한이 부여되었습니다.\n"

        msg += "🎉 환영 과정이 완료되었습니다!"
        await interaction.response.send_message(msg, ephemeral=True)

@bot.event
async def on_ready():
    print(f"봇 로그인됨: {bot.user}")
    print(f"봇이 {len(bot.guilds)}개의 서버에 연결되었습니다.")
    print("Render 배포 성공!")

@bot.event
async def on_member_join(member):
    if member.bot:
        return
        
    if member.id in processing_members:
        return
    processing_members.add(member.id)

    try:
        guild = member.guild
        settings = MESSAGES["settings"]
        
        print(f"새 멤버 입장: {member.name} (ID: {member.id})")

        channel_name = f"환영-{member.name}"
        
        existing_channels = [ch for ch in guild.channels if ch.name == channel_name]
        if existing_channels:
            print(f"이미 존재하는 채널: {channel_name}")
            return

        welcome_cat = discord.utils.get(guild.categories, name=settings["welcome_category"])
        if not welcome_cat:
            welcome_cat = await guild.create_category(settings["welcome_category"])
            print(f"환영 카테고리 생성: {settings['welcome_category']}")

        for channel in guild.channels:
            if channel.category and channel.category.name == settings["welcome_category"]:
                continue
            try:
                if isinstance(channel, discord.TextChannel):
                    await channel.set_permissions(member, read_messages=False)
                elif isinstance(channel, discord.VoiceChannel):
                    await channel.set_permissions(member, view_channel=True, connect=True)
            except:
                pass

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        welcome_channel = await guild.create_text_channel(
            channel_name, 
            overwrites=overwrites, 
            category=welcome_cat,
            topic=f"{member.name}님의 환영 채널"
        )
        
        print(f"환영 채널 생성: {welcome_channel.name}")

        welcome = MESSAGES["welcome_messages"]["initial_welcome"]
        embed1 = discord.Embed(
            title=welcome["title"], 
            description=welcome["description"], 
            color=int(welcome["color"], 16)
        )
        embed1.add_field(name="📋 서버 규칙", value=welcome.get("field_value", "서버 규칙을 확인해주세요!"), inline=False)
        embed1.set_footer(text=f"환영합니다, {member.name}님!")
        
        await welcome_channel.send(embed=embed1, view=InitialWelcomeView(member.id))

        await asyncio.sleep(5)

        adaptation = MESSAGES["welcome_messages"]["adaptation_check"]
        embed2 = discord.Embed(
            title=adaptation["title"], 
            description=adaptation["description"].format(member_mention=member.mention), 
            color=int(adaptation["color"], 16)
        )
        embed2.add_field(name=adaptation["field_name"], value="이 버튼은 본인만 사용할 수 있습니다.", inline=False)
        embed2.set_footer(text="서버 적응 상태를 확인해주세요!")
        
        await welcome_channel.send(embed=embed2, view=AdaptationCheckView(member.id))

    except Exception as e:
        print(f"환영 메시지 생성 오류: {e}")
        import traceback
        traceback.print_exc()
    finally:
        processing_members.discard(member.id)

@bot.event
async def on_member_remove(member):
    if member.bot:
        return
        
    try:
        guild = member.guild
        channel_name = f"환영-{member.name}"
        
        welcome_channel = discord.utils.get(guild.channels, name=channel_name)
        if welcome_channel:
            await welcome_channel.delete()
            print(f"멤버 퇴장으로 환영 채널 삭제: {channel_name}")
    except Exception as e:
        print(f"멤버 퇴장 시 채널 삭제 오류: {e}")

@bot.command(name="상태")
async def status(ctx):
    guild = ctx.guild
    embed = discord.Embed(
        title="🤖 봇 상태",
        description="Discord 환영 봇의 현재 상태입니다.",
        color=0x00ff00
    )
    embed.add_field(name="서버 이름", value=guild.name, inline=True)
    embed.add_field(name="멤버 수", value=guild.member_count, inline=True)
    embed.add_field(name="채널 수", value=len(guild.channels), inline=True)
    embed.add_field(name="현재 처리 중인 멤버", value=len(processing_members), inline=True)
    embed.add_field(name="봇 지연시간", value=f"{round(bot.latency * 1000)}ms", inline=True)
    embed.add_field(name="배포 플랫폼", value="Render", inline=True)
    
    await ctx.send(embed=embed)

# 메인 실행부
if __name__ == "__main__":
    # Flask 서버를 별도 스레드에서 실행 (Render 웹 서비스용)
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Discord 봇 실행
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ DISCORD_TOKEN 환경 변수가 설정되지 않았습니다.")
        print("Render 대시보드에서 환경 변수를 확인해주세요.")
        exit(1)
    
    try:
        bot.run(token)
    except Exception as e:
        print(f"봇 실행 중 오류 발생: {e}")
        exit(1)
