import discord
from discord.ext import commands
import os
import asyncio
import json
import re
from flask import Flask
import threading
import time
import logging

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
        logger.error("❌ messages.json 파일을 찾을 수 없습니다.")
        return None

MESSAGES = load_messages()
if not MESSAGES:
    logger.error("❌ 메시지 설정 파일을 로드할 수 없습니다.")
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

def get_clean_name(display_name):
    """닉네임에서 성별 접두사를 제거한 깨끗한 이름 반환"""
    return re.sub(r'^\((?:단팥빵|메론빵)\)\s*', '', display_name).strip()

def has_gender_prefix(display_name):
    """닉네임에 성별 접두사가 있는지 확인"""
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
    """성별 역할에 따라 닉네임에 접두사를 추가"""
    try:
        # 이미 접두사가 있는지 확인
        if has_gender_prefix(member.display_name):
            logger.info(f"{member.name}님은 이미 성별 접두사가 있습니다.")
            return "already_has_prefix"
        
        # 성별 역할 찾기
        male_role = discord.utils.get(member.guild.roles, name="남자")
        female_role = discord.utils.get(member.guild.roles, name="여자")
        
        # 권한 확인
        if not member.guild.me.guild_permissions.manage_nicknames:
            logger.error("봇에게 닉네임 관리 권한이 없습니다.")
            return "no_permission"
        
        # 계층 확인 (봇의 역할이 대상 멤버보다 높아야 함)
        if member.top_role >= member.guild.me.top_role:
            logger.error(f"{member.name}님의 역할이 봇보다 높아서 닉네임을 변경할 수 없습니다.")
            return "hierarchy_error"
        
        # 서버 소유자인지 확인
        if member.id == member.guild.owner_id:
            logger.error("서버 소유자의 닉네임은 변경할 수 없습니다.")
            return "owner_error"
        
        # 현재 닉네임 가져오기
        current_nick = member.display_name
        clean_name = get_clean_name(current_nick)
        
        # 닉네임이 너무 긴지 확인 (Discord 제한: 32자)
        if len(clean_name) > 26:  # 접두사 공간 확보
            clean_name = clean_name[:26]
        
        # 성별 역할에 따라 접두사 추가
        if male_role and male_role in member.roles:
            new_nick = f"{MESSAGES['settings']['male_prefix']} {clean_name}"
            await member.edit(nick=new_nick, reason="성별 역할에 따른 닉네임 접두사 추가")
            logger.info(f"남자 접두사 추가: {member.name} -> {new_nick}")
            return "male"
        elif female_role and female_role in member.roles:
            new_nick = f"{MESSAGES['settings']['female_prefix']} {clean_name}"
            await member.edit(nick=new_nick, reason="성별 역할에 따른 닉네임 접두사 추가")
            logger.info(f"여자 접두사 추가: {member.name} -> {new_nick}")
            return "female"
        else:
            logger.info(f"{member.name}님에게 성별 역할(남자/여자)이 없습니다.")
            return "no_gender_role"
            
    except discord.Forbidden:
        logger.error(f"권한 부족으로 {member.name}님의 닉네임을 변경할 수 없습니다.")
        return "forbidden"
    except discord.HTTPException as e:
        logger.error(f"HTTP 오류로 닉네임 변경 실패: {e}")
        return "http_error"
    except Exception as e:
        logger.error(f"예상치 못한 오류로 닉네임 변경 실패: {e}")
        return "unexpected_error"

async def grant_all_channel_access(member):
    """모든 채널에 대한 접근 권한을 부여"""
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
                    await channel.set_permissions(member, view_channel=True, connect=True, speak=True)
                    success_count += 1
            except Exception as e:
                logger.error(f"채널 {channel.name} 권한 설정 오류: {e}")
                error_count += 1
        
        logger.info(f"채널 권한 부여 완료 - 성공: {success_count}, 실패: {error_count}")
        return success_count > 0
        
    except Exception as e:
        logger.error(f"채널 접근 권한 부여 중 전체 오류: {e}")
        return False

async def notify_admin_rejoin(guild, member):
    """관리자에게 재입장 알림"""
    try:
        # 도라도라미 역할을 가진 관리자들에게 DM 발송
        doradori_role = discord.utils.get(guild.roles, name="도라도라미")
        if not doradori_role:
            logger.warning("도라도라미 역할을 찾을 수 없습니다.")
            return
        
        embed = discord.Embed(
            title="🔄 재입장 알림",
            description=f"**{member.mention}** ({member.name})님이 재입장했습니다.",
            color=0xFFA500,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(
            name="사용자 정보", 
            value=f"ID: {member.id}\n계정 생성일: {member.created_at.strftime('%Y-%m-%d')}", 
            inline=False
        )
        embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
        
        # 관리자들에게 DM 발송
        sent_count = 0
        for admin in doradori_role.members:
            try:
                await admin.send(embed=embed)
                sent_count += 1
            except discord.Forbidden:
                logger.warning(f"관리자 {admin.name}에게 DM을 보낼 수 없습니다.")
            except Exception as e:
                logger.error(f"관리자 {admin.name}에게 DM 발송 오류: {e}")
        
        logger.info(f"재입장 알림 발송 완료 - {sent_count}명의 관리자에게 전송")
                
    except Exception as e:
        logger.error(f"재입장 알림 전체 오류: {e}")

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
        except Exception as e:
            logger.error(f"채널 삭제 오류: {e}")

    @discord.ui.button(label="보존", style=discord.ButtonStyle.success, emoji="✅")
    async def preserve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        member_name = interaction.channel.name.replace("환영-", "")
        member = discord.utils.get(interaction.guild.members, name=member_name)
        if not member:
            await interaction.response.send_message("❌ 해당 멤버를 찾을 수 없습니다.", ephemeral=True)
            return
        
        access_granted = await grant_all_channel_access(member)
        if access_granted:
            await interaction.response.send_message("✅ 모든 채널 접근 권한이 부여되었습니다. 환영 채널이 보존되었습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ 일부 채널 접근 권한 부여에 실패했습니다.", ephemeral=True)

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
        except Exception as e:
            logger.error(f"채널 삭제 오류: {e}")

    @discord.ui.button(label="보존", style=discord.ButtonStyle.success, emoji="✅")
    async def preserve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user  # 본인이 버튼을 누르므로 interaction.user 사용
        
        # 닉네임 변경 시도
        result = await change_nickname_with_gender_prefix(member)
        
        # 채널 접근 권한 부여
        access = await grant_all_channel_access(member)

        # 결과 메시지 생성
        msg = ""
        if result == "male":
            msg += f"👦 {get_clean_name(member.display_name)} 님의 닉네임에 단팥빵 접두사가 추가되었습니다.\n"
        elif result == "female":
            msg += f"👧 {get_clean_name(member.display_name)} 님의 닉네임에 메론빵 접두사가 추가되었습니다.\n"
        elif result == "already_has_prefix":
            msg += "✅ 이미 접두사가 포함된 닉네임입니다.\n"
        elif result == "no_gender_role":
            msg += "⚠️ 성별 역할(남자/여자)이 없어서 닉네임 변경을 건너뜁니다.\n"
        elif result == "no_permission":
            msg += "❌ 봇에게 닉네임 관리 권한이 없습니다.\n"
        elif result == "hierarchy_error":
            msg += "❌ 권한 계층 문제로 닉네임을 변경할 수 없습니다.\n"
        elif result == "owner_error":
            msg += "❌ 서버 소유자의 닉네임은 변경할 수 없습니다.\n"
        elif result == "forbidden":
            msg += "❌ 권한이 부족하여 닉네임을 변경할 수 없습니다.\n"
        elif result == "http_error":
            msg += "❌ 네트워크 오류로 닉네임 변경에 실패했습니다.\n"
        else:
            msg += f"❌ 알 수 없는 오류로 닉네임 변경에 실패했습니다. (오류코드: {result})\n"

        if access:
            msg += "✅ 모든 채널 접근 권한이 부여되었습니다.\n"
        else:
            msg += "⚠️ 일부 채널 접근 권한 부여에 실패했습니다.\n"

        msg += "🎉 환영 과정이 완료되었습니다!"
        await interaction.response.send_message(msg, ephemeral=True)

@bot.event
async def on_ready():
    logger.info(f"봇 로그인됨: {bot.user}")
    logger.info(f"봇이 {len(bot.guilds)}개의 서버에 연결되었습니다.")
    
    # 각 서버의 권한 확인
    for guild in bot.guilds:
        permissions = guild.me.guild_permissions
        logger.info(f"서버 '{guild.name}' 권한 상태:")
        logger.info(f"  - 닉네임 관리: {permissions.manage_nicknames}")
        logger.info(f"  - 채널 관리: {permissions.manage_channels}")
        logger.info(f"  - 역할 관리: {permissions.manage_roles}")
        logger.info(f"  - 메시지 관리: {permissions.manage_messages}")
    
    logger.info("Render 배포 성공!")

@bot.event
async def on_member_join(member):
    if member.bot:
        return
    
    # 재입장 확인
    is_returning_member = is_rejoin(member.id, member.guild.id)
    
    # 재입장이면 관리자에게 알림
    if is_returning_member:
        await notify_admin_rejoin(member.guild, member)
    
    # 채널 생성 락 사용하여 중복 생성 방지
    async with channel_creation_lock:
        # 이미 처리 중인 멤버인지 확인
        if member.id in processing_members:
            logger.info(f"이미 처리 중인 멤버: {member.name}")
            return
        
        processing_members.add(member.id)
        
        try:
            guild = member.guild
            settings = MESSAGES["settings"]
            
            logger.info(f"새 멤버 입장: {member.name} (ID: {member.id}) - 재입장: {is_returning_member}")

            channel_name = f"환영-{member.name}"
            
            # 기존 채널 확인 - 더 엄격하게
            existing_channels = [
                ch for ch in guild.channels 
                if ch.name.lower() == channel_name.lower() and isinstance(ch, discord.TextChannel)
            ]
            
            if existing_channels:
                logger.info(f"이미 존재하는 채널 발견: {channel_name}")
                return

            # 환영 카테고리 확인/생성
            welcome_cat = discord.utils.get(guild.categories, name=settings["welcome_category"])
            if not welcome_cat:
                try:
                    welcome_cat = await guild.create_category(settings["welcome_category"])
                    logger.info(f"환영 카테고리 생성: {settings['welcome_category']}")
                except Exception as e:
                    logger.error(f"환영 카테고리 생성 오류: {e}")

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
                    logger.error(f"채널 권한 설정 오류 ({channel.name}): {e}")

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
            
            # 채널 생성 직전 마지막 중복 확인
            final_check = discord.utils.get(guild.channels, name=channel_name)
            if final_check:
                logger.info(f"채널 생성 직전 중복 발견: {channel_name}")
                return
            
            # 환영 채널 생성
            welcome_channel = await guild.create_text_channel(
                channel_name, 
                overwrites=overwrites, 
                category=welcome_cat,
                topic=f"{member.name}님의 환영 채널 {'(재입장)' if is_returning_member else ''}"
            )
            
            logger.info(f"환영 채널 생성 완료: {welcome_channel.name}")

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
            logger.error(f"환영 메시지 생성 오류: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # 처리 완료 후 제거
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
            logger.info(f"멤버 퇴장으로 환영 채널 삭제: {channel_name}")
            
        # 처리 중인 멤버 목록에서도 제거
        processing_members.discard(member.id)
        
    except Exception as e:
        logger.error(f"멤버 퇴장 시 채널 삭제 오류: {e}")

@bot.command(name="상태")
async def status(ctx):
    guild = ctx.guild
    permissions = guild.me.guild_permissions
    
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
    embed.add_field(name="입장 기록", value=f"{len(member_join_history)}개 저장됨", inline=True)
    
    # 권한 상태 추가
    permission_status = []
    permission_status.append(f"닉네임 관리: {'✅' if permissions.manage_nicknames else '❌'}")
    permission_status.append(f"채널 관리: {'✅' if permissions.manage_channels else '❌'}")
    permission_status.append(f"역할 관리: {'✅' if permissions.manage_roles else '❌'}")
    
    embed.add_field(name="🔑 봇 권한", value="\n".join(permission_status), inline=False)
    
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
            value=time_str,
            inline=False
        )
    
    await ctx.send(embed=embed)

@bot.command(name="권한체크")
async def check_permissions(ctx, member: discord.Member = None):
    """특정 멤버의 닉네임 변경 가능 여부 확인 (관리자 전용)"""
    doradori_role = discord.utils.get(ctx.guild.roles, name="도라도라미")
    if not doradori_role or doradori_role not in ctx.author.roles:
        await ctx.send("❌ 도라도라미 역할이 있는 사람만 사용할 수 있습니다.")
        return
    
    if member is None:
        await ctx.send("❌ 사용법: `!권한체크 @멤버`")
        return
    
    embed = discord.Embed(
        title="🔍 권한 체크",
        description=f"{member.mention}님의 닉네임 변경 가능 여부",
        color=0x3498db
    )
    
    # 각종 조건 확인
    conditions = []
    
    # 봇 권한
    if ctx.guild.me.guild_permissions.manage_nicknames:
        conditions.append("✅ 봇 닉네임 관리 권한")
    else:
        conditions.append("❌ 봇 닉네임 관리 권한 없음")
    
    # 계층 확인
    if member.top_role < ctx.guild.me.top_role:
        conditions.append("✅ 봇 역할이 대상보다 높음")
    else:
        conditions.append("❌ 봇 역할이 대상보다 낮거나 같음")
    
    # 서버 소유자 확인
    if member.id == ctx.guild.owner_id:
        conditions.append("❌ 서버 소유자 (변경 불가)")
    else:
        conditions.append("✅ 서버 소유자 아님")
    
    # 성별 역할 확인
    male_role = discord.utils.get(ctx.guild.roles, name="남자")
    female_role = discord.utils.get(ctx.guild.roles, name="여자")
    
    if male_role and male_role in member.roles:
        conditions.append("✅ 남자 역할 보유")
    elif female_role and female_role in member.roles:
        conditions.append("✅ 여자 역할 보유")
    else:
        conditions.append("❌ 성별 역할 없음")
    
    # 현재 닉네임 상태
    if has_gender_prefix(member.display_name):
        conditions.append("⚠️ 이미 성별 접두사 있음")
    else:
        conditions.append("✅ 성별 접두사 없음")
    
    embed.add_field(name="상태 확인", value="\n".join(conditions), inline=False)
    
    # 현재 닉네임 정보
    embed.add_field(name="현재 닉네임", value=member.display_name, inline=True)
    embed.add_field(name="정리된 이름", value=get_clean_name(member.display_name), inline=True)
    embed.add_field(name="최고 역할", value=member.top_role.name, inline=True)
    
    await ctx.send(embed=embed)

@bot.command(name="강제닉네임")
async def force_nickname(ctx, member: discord.Member = None, *, new_nick: str = None):
    """강제로 닉네임 변경 (관리자 전용)"""
    doradori_role = discord.utils.get(ctx.guild.roles, name="도라도라미")
    if not doradori_role or doradori_role not in ctx.author.roles:
        await ctx.send("❌ 도라도라미 역할이 있는 사람만 사용할 수 있습니다.")
        return
    
    if member is None or new_nick is None:
        await ctx.send("❌ 사용법: `!강제닉네임 @멤버 새로운닉네임`")
        return
    
    try:
        old_nick = member.display_name
        await member.edit(nick=new_nick, reason=f"관리자 {ctx.author.name}에 의한 강제 닉네임 변경")
        
        embed = discord.Embed(
            title="✅ 닉네임 변경 완료",
            description=f"{member.mention}님의 닉네임이 변경되었습니다.",
            color=0x00ff00
        )
        embed.add_field(name="이전 닉네임", value=old_nick, inline=True)
        embed.add_field(name="새 닉네임", value=new_nick, inline=True)
        embed.add_field(name="변경자", value=ctx.author.mention, inline=True)
        
        await ctx.send(embed=embed)
        
    except discord.Forbidden:
        await ctx.send("❌ 권한이 부족하여 닉네임을 변경할 수 없습니다.")
    except discord.HTTPException as e:
        await ctx.send(f"❌ 닉네임 변경 중 오류가 발생했습니다: {e}")
    except Exception as e:
        await ctx.send(f"❌ 예상치 못한 오류가 발생했습니다: {e}")

@bot.command(name="성별접두사")
async def add_gender_prefix(ctx, member: discord.Member = None):
    """특정 멤버에게 성별 접두사 추가 (관리자 전용)"""
    doradori_role = discord.utils.get(ctx.guild.roles, name="도라도라미")
    if not doradori_role or doradori_role not in ctx.author.roles:
        await ctx.send("❌ 도라도라미 역할이 있는 사람만 사용할 수 있습니다.")
        return
    
    if member is None:
        await ctx.send("❌ 사용법: `!성별접두사 @멤버`")
        return
    
    result = await change_nickname_with_gender_prefix(member)
    
    embed = discord.Embed(
        title="🏷️ 성별 접두사 추가",
        description=f"{member.mention}님의 닉네임 처리 결과",
        color=0x3498db
    )
    
    if result == "male":
        embed.add_field(name="결과", value="✅ 남자 접두사 추가됨", inline=False)
        embed.color = 0x3498db
    elif result == "female":
        embed.add_field(name="결과", value="✅ 여자 접두사 추가됨", inline=False)
        embed.color = 0xe91e63
    elif result == "already_has_prefix":
        embed.add_field(name="결과", value="⚠️ 이미 성별 접두사가 있습니다", inline=False)
        embed.color = 0xff9800
    elif result == "no_gender_role":
        embed.add_field(name="결과", value="❌ 성별 역할(남자/여자)이 없습니다", inline=False)
        embed.color = 0xff5722
    elif result == "no_permission":
        embed.add_field(name="결과", value="❌ 봇에게 닉네임 관리 권한이 없습니다", inline=False)
        embed.color = 0xf44336
    elif result == "hierarchy_error":
        embed.add_field(name="결과", value="❌ 권한 계층 문제 (대상의 역할이 봇보다 높음)", inline=False)
        embed.color = 0xf44336
    elif result == "owner_error":
        embed.add_field(name="결과", value="❌ 서버 소유자의 닉네임은 변경할 수 없습니다", inline=False)
        embed.color = 0xf44336
    elif result == "forbidden":
        embed.add_field(name="결과", value="❌ 권한이 부족합니다", inline=False)
        embed.color = 0xf44336
    elif result == "http_error":
        embed.add_field(name="결과", value="❌ 네트워크 오류가 발생했습니다", inline=False)
        embed.color = 0xf44336
    else:
        embed.add_field(name="결과", value=f"❌ 알 수 없는 오류 (코드: {result})", inline=False)
        embed.color = 0xf44336
    
    embed.add_field(name="현재 닉네임", value=member.display_name, inline=True)
    embed.add_field(name="요청자", value=ctx.author.mention, inline=True)
    
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
        logger.error("❌ DISCORD_TOKEN 환경 변수가 설정되지 않았습니다.")
        logger.error("Render 대시보드에서 환경 변수를 확인해주세요.")
        exit(1)
    
    try:
        bot.run(token)
    except Exception as e:
        logger.error(f"봇 실행 중 오류 발생: {e}")
        exit(1)
