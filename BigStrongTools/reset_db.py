import requests
import getpass

BASE_URL = "http://127.0.0.1:5000"


def admin_login():
    print("\nAdmin Login Required")
    username = input("Username: ")
    password = getpass.getpass("Password: ")

    try:
        res = requests.post(
            f"{BASE_URL}/v1/auth/login",
            json={"username": username, "password": password}
        )

        if res.status_code == 200:
            data = res.json()

            # Handle flexible token response shapes
            token = (
                data.get("token") or
                data.get("accessToken") or
                data.get("access_token")
            )

            if not token:
                print("Login succeeded but no token found in response:")
                print(data)
                return None

            print("Login successful\n")
            return token

        else:
            print(f"Login failed ({res.status_code}):")
            print(res.text)
            return None

    except Exception as e:
        print("Login request failed:", str(e))
        return None


def reset_database(token, dry_run=True):
    headers = {
        "Authorization": f"Bearer {token}"
    }

    try:
        url = f"{BASE_URL}/v1/admin/reset"
        if dry_run:
            url += "?dryRun=true"

        res = requests.post(url, headers=headers)

        if res.status_code == 200:
            data = res.json()

            if dry_run:
                print("DRY RUN RESULT:")
                summary = data.get("wouldDelete", {})
                for k, v in summary.items():
                    print(f"  {k}: {v}")
                print()
                return data
            else:
                print("Database wiped successfully.")
                return data

        elif res.status_code == 403:
            print("Forbidden — you are not an admin.")
        else:
            print(f"Failed ({res.status_code}):")
            print(res.text)

    except Exception as e:
        print("Request failed:", str(e))


if __name__ == "__main__":
    token = admin_login()
    if not token:
        exit()

    # Step 1: Dry run
    result = reset_database(token, dry_run=True)
    if not result:
        exit()

    # Step 2: Confirm destructive action
    confirm = input("Type 'DELETE' to wipe ALL data: ")
    if confirm == "DELETE":
        reset_database(token, dry_run=False)
    else:
        print("Aborted.")