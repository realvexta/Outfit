from flask import Flask, request, jsonify
from flask_caching import Cache
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import requests
import time
import logging
from datetime import datetime
import my_pb2
import output_pb2
import GetOutfit_pb2
try:
    from danger_ff_version_updater import get_categories
    HAS_UPDATER = True
except ImportError:
    HAS_UPDATER = False
    print("⚠️ danger_ff_version_updater not installed. Using static config.")

STATIC_CONFIG = {
    "IND": {
        "client_url": "client.ind.freefiremobile.com",
        "server_url": "https://loginbp.ggpolarbear.com",
        "release_version": "OB53",
        "client_version": "1.123.10"
    },
    "AMERICA": {
        "client_url": "client.us.freefiremobile.com",
        "server_url": "https://loginbp.ggpolarbear.com",
        "release_version": "OB53",
        "client_version": "1.123.10"
    },
    "OTHERS": {
        "client_url": "clientbp.ggpolarbear.com",
        "server_url": "https://loginbp.ggpolarbear.com",
        "release_version": "OB53",
        "client_version": "1.123.10"
    }
}

version_config = {}
last_update = 0
UPDATE_INTERVAL = 24 * 3600

def update_version_config():
    global version_config, last_update
    if HAS_UPDATER:
        try:
            categories = get_categories()
            version_config = {k.upper(): v for k, v in categories.items()}
            last_update = time.time()
            logging.info("Version config updated")
        except Exception as e:
            logging.error(f"Updater failed: {e}")
            if not version_config:
                version_config = STATIC_CONFIG
    else:
        version_config = STATIC_CONFIG

def get_version_config(region):
    global version_config, last_update
    if time.time() - last_update > UPDATE_INTERVAL:
        update_version_config()
    if region == "IND":
        return version_config.get("IND", STATIC_CONFIG["IND"])
    elif region in ["BR", "US", "NA", "SAC"]:
        return version_config.get("AMERICA", STATIC_CONFIG["AMERICA"])
    else:
        return version_config.get("OTHERS", STATIC_CONFIG["OTHERS"])

# ------------------------------
# Flask app
# ------------------------------
app = Flask(__name__)
cache = Cache(config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 25200})  # 7 hours
cache.init_app(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AES_KEY = b'Yg&tc%DEuh6%Zc^8'
AES_IV  = b'6oyZDr22E3ychjM%'

def encrypt_message(plaintext: bytes) -> bytes:
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    return cipher.encrypt(pad(plaintext, AES.block_size))

# ---------- Credentials mapping ----------
REGION_CRED = {
    "IND":    {"uid": "4765721896", "password": "A3FE934240965FD0092B6A4B87FDDFA282F97119095288100B7645EB3AE77F8B"},
    "AMERICA":{"uid": "4765721099", "password": "C60B035E09E4F41DDE31921CD4338BEF751A14532B3FFEC044056BB6C1F33763"},
    "OTHERS": {"uid": "4765722285", "password": "F3FF4573502AF0824CD485193077D1C9415BF2D569E18B699A070DE6F1846068"}
}

def get_cred(region):
    if region == "IND":
        return REGION_CRED["IND"]
    elif region in ["BR","US","NA","SAC"]:
        return REGION_CRED["AMERICA"]
    return REGION_CRED["OTHERS"]

def get_jwt_token(region):
    cache_key = f"jwt_{region}"
    tok = cache.get(cache_key)
    if tok:
        return tok

    cred = get_cred(region)
    cfg = get_version_config(region)

    # ---------- OAuth ----------
    oauth_resp = requests.post(
        "https://100067.connect.garena.com/oauth/guest/token/grant",
        data={
            'uid': cred['uid'],
            'password': cred['password'],
            'response_type': "token",
            'client_type': "2",
            'client_secret': "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3",
            'client_id': "100067"
        },
        headers={'User-Agent': 'GarenaMSDK/4.0.19P9'},
        timeout=10
    )
    if oauth_resp.status_code != 200:
        logger.error("OAuth failed")
        return None
    oauth_data = oauth_resp.json()
    access_token = oauth_data.get('access_token')
    open_id = oauth_data.get('open_id')
    if not access_token or not open_id:
        return None

    # ---------- MajorLogin (only required fields) ----------
    game_data = my_pb2.GameData()
    game_data.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    game_data.game_name = "free fire"
    game_data.game_version = 1
    game_data.version_code = cfg["client_version"]
    game_data.open_id = open_id
    game_data.access_token = access_token
    game_data.platform_type = 4
    game_data.field_99 = "4"
    game_data.field_100 = "4"

    serialized = game_data.SerializeToString()
    encrypted_req = encrypt_message(serialized)

    major_url = f"{cfg['server_url'].rstrip('/')}/MajorLogin"
    headers = {
        "User-Agent": "Dalvik/2.1.0",
        "Content-Type": "application/octet-stream",
        "X-Unity-Version": "2018.4.11f1",
        "X-GA": "v1 1",
        "ReleaseVersion": cfg["release_version"]
    }
    try:
        resp = requests.post(major_url, data=encrypted_req, headers=headers, timeout=10)
        if resp.status_code == 200:
            # Response is plain protobuf (no decryption)
            msg = output_pb2.Garena_420()
            msg.ParseFromString(resp.content)
            if msg.token:
                cache.set(cache_key, msg.token, timeout=25200)
                return msg.token
    except Exception as e:
        logger.error(f"MajorLogin error: {e}")
    return None
def fetch_outfit(jwt_token, account_id, region):
    req = GetOutfit_pb2.CSGetOutfitReq()
    req.AccountId = account_id
    plaintext = req.SerializeToString()
    encrypted_body = encrypt_message(plaintext)

    cfg = get_version_config(region)
    base_url = cfg["client_url"].rstrip('/')
    url = f"https://{base_url}/GetAccountOutfit"

    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Host": base_url,
        "ReleaseVersion": cfg["release_version"],
        "User-Agent": "Free Fire MAX/2019117050 CFNetwork/3860.200.71 Darwin/25.1.0",
        "X-GA": "v1 1",
        "X-Unity-Version": "2022.3.47f1"
    }
    resp = requests.post(url, data=encrypted_body, headers=headers, timeout=15)
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}", "detail": resp.text[:200]}

    res = GetOutfit_pb2.CSGetOutfitRes()
    res.ParseFromString(resp.content)

    return {
        "WeaponSkinShows": list(res.WeaponSkinShows),
        "ProfileInfo": {
            "CharacterId": res.ProfileInfo.CharacterId,
            "SkinColor": res.ProfileInfo.SkinColor,
            "Clothes": list(res.ProfileInfo.Clothes),
            "Skills": [
                {
                    **({"SlotNo": s.SlotNo} if s.HasField('SlotNo') else {}),
                    "SkillId": s.SkillId
                }
                for s in res.ProfileInfo.EquippedSkills
            ],
            "IsSelected": res.ProfileInfo.IsSelected if res.ProfileInfo.HasField('IsSelected') else None,
            "IsAwakenSelected": res.ProfileInfo.IsAwakenSelected if res.ProfileInfo.HasField('IsAwakenSelected') else None
        }
    }

@app.route('/outfit', methods=['GET'])
def outfit():
    uid = request.args.get('uid')
    region = request.args.get('region')
    if not uid or not region:
        return jsonify({"error": "Missing uid or region"}), 400
    region = region.upper()
    jwt_token = get_jwt_token(region)
    if not jwt_token:
        return jsonify({"error": "JWT generation failed"}), 500
    result = fetch_outfit(jwt_token, int(uid), region)
    result["credit"] = "t.me/danger_ff_dev"
    return jsonify(result)

@app.route('/health')
def health():
    return jsonify({"status": "ok"})

update_version_config()
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=1080)