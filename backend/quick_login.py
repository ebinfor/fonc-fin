import json
import urllib.request

url_login = "https://backend-production-39c8.up.railway.app/v1/auth/login"
url_mfa = "https://backend-production-39c8.up.railway.app/v1/auth/verify-mfa"

print("🔄 Connexion initiale en cours...")
data_login = json.dumps({"email": "kader@exemple.com", "password": "MonMotDePasseSecurise123"}).encode()
req1 = urllib.request.Request(url_login, data=data_login, headers={"Content-Type": "application/json"})

try:
    with urllib.request.urlopen(req1) as res:
        res_data = json.loads(res.read().decode())
        mfa_token = res_data.get("mfa_token")
except Exception as e:
    print(f"❌ Impossible de se connecter : {e}")
    exit()

# Le script t'attend ici pour que le token reste frais au moment de l'envoi
code = input("🔢 Entre ton code MFA actuel à 6 chiffres : ")

print("🚀 Validation MFA immédiate...")
data_mfa = json.dumps({"code": code, "mfa_token": mfa_token}).encode()
req2 = urllib.request.Request(url_mfa, data=data_mfa, headers={"Content-Type": "application/json"})

try:
    with urllib.request.urlopen(req2) as res:
        final_data = json.loads(res.read().decode())
        print("\n✅ TOKENS REÇUS AVEC SUCCÈS !")
        print("Voici ton Access Token final pour ton curl :\n")
        print(final_data.get("access_token"))
except Exception as e:
    print(f"\n❌ Erreur lors de la validation : {e}")