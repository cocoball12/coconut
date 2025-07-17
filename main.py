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
# 활동 추적을 위한 변수
member_activity = {}  # {user_id: {'last_activity': timestamp, 'channel_id': channel_id}}

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
        # ㅇㄹㅇㄹ 역할을 가진 관리자들에게 DM 발송
        admin_role = discord.utils.get(guild.roles, name="ㅇㄹㅇㄹ")
        if not admin_role:
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
        for admin in admin_role.members:
            try:
                await admin.send(embed=embed)
            except discord.Forbidden:
                print(f"관리자 {admin.name}에게 DM을 보낼 수 없습니다.")
            except Exception as e:
                print(f"관리자 DM 발송 오류: {e}")
                
    except Exception as e:
        print(f"재입장 알림 오류: {e}")

async def send_second_guide_and_activity_check(member, welcome_channel):
    """두 번째 안내문 전송 및 활동 체크 시작"""
    try:
        # 두 번째 안내문 전송
        second_guide = MESSAGES["welcome_messages"]["second_guide"]
        embed = discord.Embed(
            title=second_guide["title"],
            description=second_guide["description"].format(member=member.mention),
            color=int(second_guide["color"], 16)
        )
        
        # 필드가 있다면 추가
        if "fields" in second_guide:
            for field in second_guide["fields"]:
                embed.add_field(
                    name=field["name"],
                    value=field["value"],
                    inline=field["inline"]
                )
        
        # 푸터가 있다면 추가
        if "footer" in second_guide:
            embed.set_footer(text=second_guide["footer"])
        
        # 적응 확인 버튼 추가
        view = AdaptationCheckView(member.id)
        await welcome_channel.send(embed=embed, view=view)
        print(f"두 번째 안내문 전송 완료: {member.name}")
        
        # 활동 체크 시작 (10초 + 7초 + 15초 = 32초 시스템)
        await check_member_activity(member.id, member.guild.id)
        
    except Exception as e:
        print(f"두 번째 안내문 전송 오류: {e}")
        import traceback
        traceback.print_exc()

async def check_member_activity(member_id, guild_id):
    """멤버 활동 체크 및 자동 강퇴 시스템"""
    try:
        guild = bot.get_guild(guild_id)
        if not guild:
            return
        
        member = guild.get_member(member_id)
        if not member:
            return
        
        # 10초 대기 후 첫 번째 알림
        await asyncio.sleep(10)
        
        welcome_channel = discord.utils.get(guild.channels, name=f"애정듬뿍-{member.display_name}")
        if not welcome_channel:
            return
        
        # 첫 번째 알림 - 적응 확인
        embed1 = discord.Embed(
            title="⏰ 적응 상태 확인",
            description=f"{member.mention}님, 서버에 적응하고 계신가요?\n\n**적응 완료** 버튼을 눌러주세요!",
            color=0xFFFF00
        )
        embed1.set_footer(text="버튼을 누르지 않으면 추가 알림이 발송됩니다.")
        
        await welcome_channel.send(embed=embed1)
        print(f"첫 번째 알림 발송: {member.name}")
        
        # 7초 대기 후 두 번째 알림
        await asyncio.sleep(7)
        
        # 멤버가 아직 있는지 확인
        member = guild.get_member(member_id)
        if not member:
            return
        
        welcome_channel = discord.utils.get(guild.channels, name=f"애정듬뿍-{member.display_name}")
        if not welcome_channel:
            return
        
        # 활동 확인 (메시지 전송, 음성 채널 참여 등)
        has_activity = False
        
        # 최근 메시지 확인
        async for message in welcome_channel.history(limit=10):
            if message.author.id == member_id and message.created_at > discord.utils.utcnow() - discord.utils.timedelta(seconds=17):
                has_activity = True
                break
        
        # 음성 채널 활동 확인
        if member.voice and member.voice.channel:
            has_activity = True
        
        if not has_activity:
            # 두 번째 알림 - 활동 없음 경고
            embed2 = discord.Embed(
                title="⚠️ 활동 없음 경고",
                description=f"{member.mention}님, 채팅이나 음성 채널에서 활동이 감지되지 않았습니다.\n\n**15초 후 자동으로 강퇴됩니다.**\n\n지금 즉시 활동해주세요!",
                color=0xFF0000
            )
            embed2.set_footer(text="채팅 또는 음성 채널 참여로 활동을 보여주세요.")
            
            await welcome_channel.send(embed=embed2)
            print(f"두 번째 경고 발송: {member.name}")
            
            # 15초 대기 후 강퇴
            await asyncio.sleep(15)
            
            # 다시 한 번 멤버 확인
            member = guild.get_member(member_id)
            if not member:
                return
            
            # 최종 활동 확인
            final_activity = False
            
            # 최근 메시지 확인 (총 32초 동안)
            async for message in welcome_channel.history(limit=20):
                if message.author.id == member_id and message.created_at > discord.utils.utcnow() - discord.utils.timedelta(seconds=32):
                    final_activity = True
                    break
            
            # 음성 채널 활동 확인
            if member.voice and member.voice.channel:
                final_activity = True
            
            if not final_activity:
                # 강퇴 실행
                try:
                    await member.kick(reason="환영 과정에서 활동 없음으로 인한 자동 강퇴")
                    print(f"자동 강퇴 완료: {member.name}")
                    
                    # 관리자에게 알림
                    admin_role = discord.utils.get(guild.roles, name="ㅇㄹㅇㄹ")
                    if admin_role:
                        embed_kick = discord.Embed(
                            title="🚫 자동 강퇴 알림",
                            description=f"**{member.name}** (ID: {member_id})님이 활동 없음으로 자동 강퇴되었습니다.",
                            color=0xFF0000,
                            timestamp=discord.utils.utcnow()
                        )
                        embed_kick.add_field(name="강퇴 사유", value="환영 과정에서 32초간 활동 없음", inline=False)
                        
                        for admin in admin_role.members:
                            try:
                                await admin.send(embed=embed_kick)
                            except:
                                pass
                    
                except discord.Forbidden:
                    print(f"강퇴 권한 없음: {member.name}")
                except Exception as e:
                    print(f"강퇴 오류: {e}")
            else:
                print(f"활동 감지됨, 강퇴 취소: {member.name}")
        else:
            print(f"초기 활동 감지됨: {member.name}")
            
    except Exception as e:
        print(f"활동 체크 오류: {e}")
        import traceback
        traceback.print_exc()

class InitialWelcomeView(discord.ui.View):
    def __init__(self, member_id):
        super().__init__(timeout=None)
        self.member_id = member_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        admin_role = discord.utils.get(interaction.guild.roles, name="ㅇㄹㅇㄹ")
        if not admin_role or admin_role not in interaction.user.roles:
            await interaction.response.send_message("❌ ㅇㄹㅇㄹ 역할이 있는 사람만 사용할 수 있습니다.", ephemeral=True)
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
        # 채널 이름에서 멤버 찾기 (애정듬뿍-{닉네임} 형태)
        member_name = interaction.channel.name.replace("애정듬뿍-", "")
        member = None
        
        # 서버 닉네임으로 찾기
        for guild_member in interaction.guild.members:
            if guild_member.display_name == member_name:
                member = guild_member
                break
        
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

    @discord.ui.button(label="적응 완료", style=discord.ButtonStyle.success, emoji="✅")
    async def adaptation_complete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
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
        print(f"  - 멤버 추방: {permissions.kick_members}")
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
    
    # 고유한 채널 식별자 생성 (서버 닉네임 사용)
    channel_name = f"애정듬뿍-{member.display_name}"
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
            print(f"생성할 채널 이름: {channel_name}")

            # 환영 카테고리 확인/생성
            welcome_cat = discord.utils.get(guild.categories, name=settings["welcome_category"])
            if not welcome_cat:
                welcome_cat = await guild.create_category(settings["welcome_category"])
                print(f"환영 카테고리 생성: {settings['welcome_category']}")
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
            
           # 잘린 부분부터 이어서 작성

            # ㅇㄹㅇㄹ 역할에도 접근 권한 부여
            admin_role = discord.utils.get(guild.roles, name="ㅇㄹㅇㄹ")
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            # 환영 채널 생성
            welcome_channel = await guild.create_text_channel(
                channel_name,
                category=welcome_cat,
                overwrites=overwrites
            )
            
            print(f"환영 채널 생성: {welcome_channel.name}")

            # 첫 번째 안내문 전송
            welcome_msg = MESSAGES["welcome_messages"]["first_guide"]
            embed = discord.Embed(
                title=welcome_msg["title"],
                description=welcome_msg["description"].format(member=member.mention),
                color=int(welcome_msg["color"], 16)
            )
            
            # 필드들 추가
            for field in welcome_msg["fields"]:
                embed.add_field(
                    name=field["name"],
                    value=field["value"],
                    inline=field["inline"]
                )
            
            embed.set_footer(text=welcome_msg["footer"])
            embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
            
            # 재입장 알림 추가
            if is_returning_member:
                embed.add_field(
                    name="🔄 재입장 알림", 
                    value="이전에 서버에 참여했던 기록이 있습니다.", 
                    inline=False
                )
            
            # 관리자 버튼 추가
            view = InitialWelcomeView(member.id)
            await welcome_channel.send(embed=embed, view=view)
            
            # 두 번째 안내문과 활동 체크 시작 (5초 후)
            await asyncio.sleep(5)
            await send_second_guide_and_activity_check(member, welcome_channel)
            
        except Exception as e:
            print(f"환영 프로세스 오류: {e}")
            import traceback
            traceback.print_exc()
        
        finally:
            # 처리 완료 표시
            processing_members.discard(unique_identifier)
            creating_channels.discard(channel_name)

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    # 환영 채널에서의 활동 추적
    if message.channel.name.startswith("환영-"):
        member_id = message.author.id
        member_activity[member_id] = {
            'last_activity': time.time(),
            'channel_id': message.channel.id
        }
        print(f"활동 감지: {message.author.name} - 메시지 전송")
    
    await bot.process_commands(message)

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    
    # 음성 채널 활동 추적
    if after.channel:  # 음성 채널에 입장
        member_activity[member.id] = {
            'last_activity': time.time(),
            'channel_id': after.channel.id
        }
        print(f"활동 감지: {member.name} - 음성 채널 입장")

# Flask 앱 실행
flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()

# 봇 토큰으로 실행
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    print("❌ DISCORD_TOKEN 환경 변수가 설정되지 않았습니다.")
    exit(1)

bot.run(TOKEN)
