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

# 처리 중인 멤버들과 재입장 추적을 위한 전역 변수
processing_members = set()
member_join_history = {}  # {user_id: [join_timestamps]}
channel_creation_lock = asyncio.Lock()
# 채널 생성 추적을 위한 추가 변수
creating_channels = set()  # 현재 생성 중인 채널 이름들

def get_clean_name(display_name):
    return re.sub(r'^\((?:단팥빵|메론빵)\)\s*', '', display_name).strip()

def has_gender_prefix(display_name):
    return bool(re.match(r'^\((?:단팥빵|메론빵)\)', display_name))

def is_rejoin(user_id, guild_id):
    """사용자가 재입장인지 확인"""
    key = f"{user_id}_{guild_id}"
    current_time = time.time()
    
    if key not in member_join_history:
        member_join_history[key] = []
    
    # 24시간 이내의 입장 기록만 유지
    member_join_history[key] = [
        timestamp for timestamp in member_join_history[key] 
        if current_time - timestamp < 86400  # 24시간 = 86400초
    ]
    
    # 이전 입장 기록이 있으면 재입장
    is_returning = len(member_join_history[key]) > 0
    
    # 현재 입장 시간 추가
    member_join_history[key].append(current_time)
    
    return is_returning

async def change_nickname_with_gender_prefix(member):
    try:
        print(f"=== 닉네임 변경 시도 시작 ===")
        print(f"대상: {member.name} (ID: {member.id})")
        print(f"현재 닉네임: {member.display_name}")
        print(f"서버 소유자: {member.guild.owner}")
        print(f"멤버가 서버 소유자인가: {member.id == member.guild.owner_id}")
        
        # 서버 소유자는 닉네임 변경 불가
        if member.id == member.guild.owner_id:
            print("❌ 서버 소유자의 닉네임은 변경할 수 없습니다.")
            return "server_owner"
        
        # 이미 접두사가 있는지 확인
        if has_gender_prefix(member.display_name):
            print("✅ 이미 접두사가 있음")
            return "already_has_prefix"
        
        # 성별 역할 가져오기
        male = discord.utils.get(member.guild.roles, name="남자")
        female = discord.utils.get(member.guild.roles, name="여자")
        
        print(f"남자 역할: {male}")
        print(f"여자 역할: {female}")
        print(f"멤버 역할: {[role.name for role in member.roles]}")
        
        # 깨끗한 이름 가져오기
        clean_name = get_clean_name(member.display_name)
        print(f"깨끗한 이름: {clean_name}")
        
        # 새 닉네임 생성
        new_nickname = None
        gender_type = None
        
        if male and male in member.roles:
            prefix = MESSAGES['settings']['male_prefix']
            new_nickname = f"{prefix} {clean_name}"
            gender_type = "male"
            print(f"남자 역할 확인 - 새 닉네임: {new_nickname}")
        elif female and female in member.roles:
            prefix = MESSAGES['settings']['female_prefix']
            new_nickname = f"{prefix} {clean_name}"
            gender_type = "female"
            print(f"여자 역할 확인 - 새 닉네임: {new_nickname}")
        else:
            print("❌ 성별 역할이 없음")
            return "no_gender_role"
        
        # 닉네임 길이 확인 (Discord 제한: 32자)
        if len(new_nickname) > 32:
            # 너무 길면 이름을 줄임
            prefix = MESSAGES['settings'][f'{gender_type}_prefix']
            max_name_length = 32 - len(f"{prefix} ")
            truncated_name = clean_name[:max_name_length].strip()
            new_nickname = f"{prefix} {truncated_name}"
            print(f"닉네임이 너무 길어서 줄임: {new_nickname}")
        
        # 봇의 권한 확인 - 개선된 로직
        bot_permissions = member.guild.me.guild_permissions
        print(f"봇 권한 - 닉네임 관리: {bot_permissions.manage_nicknames}")
        print(f"봇 권한 - 관리자: {bot_permissions.administrator}")
        
        # 관리자 권한이 있으면 닉네임 변경 가능
        if bot_permissions.administrator:
            print("✅ 봇이 관리자 권한을 가지고 있음")
        elif not bot_permissions.manage_nicknames:
            print("❌ 봇에게 닉네임 관리 권한이 없습니다.")
            return "no_permission"
        
        # 역할 순위 확인 - 관리자 권한이 있으면 스킵
        if not bot_permissions.administrator:
            bot_top_role = member.guild.me.top_role
            member_top_role = member.top_role
            
            print(f"봇 최고 역할: {bot_top_role.name} (위치: {bot_top_role.position})")
            print(f"멤버 최고 역할: {member_top_role.name} (위치: {member_top_role.position})")
            
            if member_top_role >= bot_top_role:
                print(f"❌ {member.name}님의 역할이 봇보다 높거나 같아서 닉네임 변경 불가")
                return "higher_role"
        
        # 닉네임 변경 시도
        print(f"닉네임 변경 시도: {member.display_name} -> {new_nickname}")
        await member.edit(nick=new_nickname)
        print(f"✅ 닉네임 변경 성공!")
        return gender_type
        
    except discord.Forbidden as e:
        print(f"❌ 권한 부족으로 닉네임 변경 실패: {e}")
        return "forbidden"
    except discord.HTTPException as e:
        print(f"❌ HTTP 오류로 닉네임 변경 실패: {e}")
        print(f"HTTP 오류 코드: {e.status}")
        print(f"HTTP 오류 메시지: {e.text}")
        return "http_error"
    except Exception as e:
        print(f"❌ 닉네임 변경 중 예상치 못한 오류: {e}")
        import traceback
        traceback.print_exc()
        return "error"

async def grant_all_channel_access(member):
    try:
        success_count = 0
        error_count = 0
        
        for channel in member.guild.channels:
            # 환영 카테고리는 건너뛰기
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
    """관리자에게 재입장 알림"""
    try:
        # 도라도라미 역할을 가진 관리자들에게 DM 발송
        doradori_role = discord.utils.get(guild.roles, name="도라도라미")
        if not doradori_role:
            return
        
        embed = discord.Embed(
            title="🔄 재입장 알림",
            description=f"**{member.mention}** ({member.name})님이 재입장했습니다.",
            color=0xFFA500,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="사용자 정보", value=f"ID: {member.id}\n계정 생성일: {member.created_at.strftime('%Y-%m-%d')}", inline=False)
        embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
        
        # 관리자들에게 DM 발송
        for admin in doradori_role.members:
            try:
                await admin.send(embed=embed)
            except discord.Forbidden:
                print(f"관리자 {admin.name}에게 DM을 보낼 수 없습니다.")
            except Exception as e:
                print(f"관리자 DM 발송 오류: {e}")
                
    except Exception as e:
        print(f"재입장 알림 오류: {e}")

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
            msg += "✅ 이미 접두사가 포함된 닉네임입니다.\n"
        elif result == "no_gender_role":
            msg += "⚠️ 성별 역할(남자/여자)이 없어서 닉네임 변경을 건너뜁니다.\n"
        elif result == "server_owner":
            msg += "⚠️ 서버 소유자의 닉네임은 변경할 수 없습니다.\n"
        elif result == "no_permission":
            msg += "❌ 봇에게 닉네임 관리 권한이 없습니다.\n서버 관리자에게 봇 권한을 확인해달라고 요청하세요.\n"
        elif result == "higher_role":
            msg += "❌ 회원님의 역할이 봇보다 높아서 닉네임 변경이 불가능합니다.\n서버 관리자에게 봇 역할 위치를 조정해달라고 요청하세요.\n"
        elif result == "forbidden":
            msg += "❌ 권한 부족으로 닉네임 변경에 실패했습니다.\n서버 관리자에게 봇 권한을 확인해달라고 요청하세요.\n"
        elif result == "http_error":
            msg += "❌ 네트워크 오류로 닉네임 변경에 실패했습니다.\n잠시 후 다시 시도해주세요.\n"
        else:
            msg += f"❌ 닉네임 변경에 실패했습니다.\n오류 코드: {result}\n서버 관리자에게 문의하세요.\n"

        if access:
            msg += "✅ 모든 채널 접근 권한이 부여되었습니다.\n"
        else:
            msg += "⚠️ 일부 채널 접근 권한 부여에 실패했을 수 있습니다.\n"

        msg += "🎉 환영 과정이 완료되었습니다!"
        await interaction.response.send_message(msg, ephemeral=True)

@bot.event
async def on_ready():
    print(f"봇 로그인됨: {bot.user}")
    print(f"봇이 {len(bot.guilds)}개의 서버에 연결되었습니다.")
    
    # 봇의 권한 확인
    for guild in bot.guilds:
        permissions = guild.me.guild_permissions
        print(f"서버 '{guild.name}'에서의 봇 권한:")
        print(f"  - 닉네임 관리: {permissions.manage_nicknames}")
        print(f"  - 채널 관리: {permissions.manage_channels}")
        print(f"  - 역할 관리: {permissions.manage_roles}")
        print(f"  - 메시지 관리: {permissions.manage_messages}")
        print(f"  - 관리자: {permissions.administrator}")
    
    print("Render 배포 성공!")

@bot.event
async def on_member_join(member):
    if member.bot:
        return
    
    # 재입장 확인
    is_returning_member = is_rejoin(member.id, member.guild.id)
    
    # 재입장이면 관리자에게 알림
    if is_returning_member:
        await notify_admin_rejoin(member.guild, member)
    
    # 고유한 채널 식별자 생성
    channel_name = f"환영-{member.name}"
    unique_identifier = f"{member.id}_{member.guild.id}"
    
    # 채널 생성 락 사용하여 중복 생성 방지
    async with channel_creation_lock:
        # 이미 처리 중인 멤버인지 확인
        if unique_identifier in processing_members:
            print(f"이미 처리 중인 멤버: {member.name}")
            return
        
        # 현재 생성 중인 채널인지 확인
        if channel_name in creating_channels:
            print(f"이미 생성 중인 채널: {channel_name}")
            return
        
        # 기존 채널 확인
        existing_channel = discord.utils.get(member.guild.channels, name=channel_name)
        if existing_channel:
            print(f"이미 존재하는 채널: {channel_name}")
            return
        
        # 처리 시작 표시
        processing_members.add(unique_identifier)
        creating_channels.add(channel_name)
        
        try:
            guild = member.guild
            settings = MESSAGES["settings"]
            
            print(f"새 멤버 입장: {member.name} (ID: {member.id}) - 재입장: {is_returning_member}")

            # 환영 카테고리 확인/생성
            welcome_cat = discord.utils.get(guild.categories, name=settings["welcome_category"])
            if not welcome_cat:
                welcome_cat = await guild.create_category(settings["welcome_category"])
                print(f"환영 카테고리 생성: {settings['welcome_category']}")

            # 모든 채널들에 대한 접근 권한 설정
            for channel in guild.channels:
                if channel.category and channel.category.name == settings["welcome_category"]:
                    continue
                try:
                    if isinstance(channel, discord.TextChannel):
                        # 텍스트 채널은 완전히 차단
                        await channel.set_permissions(member, read_messages=False, send_messages=False)
                    elif isinstance(channel, discord.VoiceChannel):
                        # 음성 채널은 보이고 접속 가능하도록 설정
                        await channel.set_permissions(member, view_channel=True, connect=True, speak=True)
                except Exception as e:
                    print(f"채널 권한 설정 오류 ({channel.name}): {e}")

            # 환영 채널 권한 설정
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                member: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            
            # 도라도라미 역할에도 접근 권한 부여
            doradori_role = discord.utils.get(guild.roles, name="도라도라미")
            if doradori_role:
                overwrites[doradori_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            
            # 환영 채널 생성
            welcome_channel = await guild.create_text_channel(
                channel_name, 
                overwrites=overwrites, 
                category=welcome_cat,
                topic=f"{member.name}님의 환영 채널 {'(재입장)' if is_returning_member else ''}"
            )
            
            print(f"환영 채널 생성 완료: {welcome_channel.name}")

            # 첫 번째 환영 메시지
            welcome = MESSAGES["welcome_messages"]["initial_welcome"]
            
            # 재입장 시 설명 텍스트 추가
            description = welcome["description"]
            if is_returning_member:
                description += "\n\n🔄 **재입장 하셨습니다!**\n이전에 서버에 계신 적이 있으시군요! 다시 돌아와 주셔서 감사합니다."
            
            embed1 = discord.Embed(
                title=f"{'🔄 ' if is_returning_member else ''}서버에 {'다시 ' if is_returning_member else ''}오신 걸 환영합니다!",
                description=description, 
                color=int(welcome["color"], 16)
            )
            embed1.add_field(
                name="📋 서버 규칙", 
                value=welcome.get("field_value", "서버 규칙을 확인해주세요!"), 
                inline=False
            )
            
            embed1.set_footer(text=f"환영합니다, {member.name}님!")
            
            await welcome_channel.send(embed=embed1, view=InitialWelcomeView(member.id))

            # 5초 대기
            await asyncio.sleep(5)

            # 두 번째 적응 확인 메시지
            adaptation = MESSAGES["welcome_messages"]["adaptation_check"]
            embed2 = discord.Embed(
                title=adaptation["title"], 
                description=adaptation["description"].format(member_mention=member.mention), 
                color=int(adaptation["color"], 16)
            )
            embed2.add_field(
                name=adaptation["field_name"], 
                value="이 버튼은 본인만 사용할 수 있습니다.", 
                inline=False
            )
            embed2.set_footer(text="서버 적응 상태를 확인해주세요!")
            
            await welcome_channel.send(embed=embed2, view=AdaptationCheckView(member.id))

        except Exception as e:
            print(f"환영 메시지 생성 오류: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # 처리 완료 후 제거
            processing_members.discard(unique_identifier)
            creating_channels.discard(channel_name)

@bot.event
async def on_member_remove(member):
    if member.bot:
        return
        
    try:
        guild = member.guild
        channel_name = f"환영-{member.name}"
        unique_identifier = f"{member.id}_{guild.id}"
        
        welcome_channel = discord.utils.get(guild.channels, name=channel_name)
        if welcome_channel:
            await welcome_channel.delete()
            print(f"멤버 퇴장으로 환영 채널 삭제: {channel_name}")
            
        # 처리 중인 멤버 목록에서도 제거
        processing_members.discard(unique_identifier)
        creating_channels.discard(channel_name)
        
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
    embed.add_field(name="생성 중인 채널", value=len(creating_channels), inline=True)
    embed.add_field(name="봇 지연시간", value=f"{round(bot.latency * 1000)}ms", inline=True)
    embed.add_field(name="배포 플랫폼", value="Render", inline=True)
    embed.add_field(name="입장 기록", value=f"{len(member_join_history)}개 저장됨", inline=True)
    
    # 봇 권한 정보 추가
    permissions = guild.me.guild_permissions
    admin_status = "✅" if permissions.administrator else "❌"
    nickname_status = "✅" if permissions.manage_nicknames else "❌"
    embed.add_field(name="관리자 권한", value=admin_status, inline=True)
    embed.add_field(name="닉네임 관리 권한", value=nickname_status, inline=True)
    
    await ctx.send(embed=embed)

@bot.command(name="입장기록")
async def join_history(ctx, user_id: int = None):
    """특정 사용자의 입장 기록 확인 (관리자 전용)"""
    # 도라도라미 역할 확인
    doradori_role = discord.utils.get(ctx.guild.roles, name="도라도라미")
    if not doradori_role or doradori_role not in ctx.author.roles:
        await ctx.send("❌ 도라도라미 역할이 있는 사람만 사용할 수 있습니다.")
        return
    
    if user_id is None:
        await ctx.send("❌ 사용법: `!입장기록 <사용자ID>`")
        return
    
    key = f"{user_id}_{ctx.guild.id}"
    if key not in member_join_history:
        await ctx.send("❌ 해당 사용자의 입장 기록이 없습니다.")
        return
    
    history = member_join_history[key]
    embed = discord.Embed(
        title="📊 입장 기록",
        description=f"<@{user_id}>님의 입장 기록",
        color=0x3498db
    )
    
    for i, timestamp in enumerate(history, 1):
        time_str = discord.utils.format_dt(discord.utils.snowflake_time(int(timestamp * 1000)), style='F')
        embed.add_field(
            name=f"{i}번째 입장",
