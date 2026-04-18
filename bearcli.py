#!/usr/bin/env python3
import argparse
import json
import os
import sys
import requests
import frontmatter
from bs4 import BeautifulSoup
# A command line front end to BearBlog.
# Modifed to use my whitelisted user-agent string.

# Config paths
CONFIG_PATH_HOME = os.path.expanduser("~/.config/bearblog/config.ini")
SESSION_PATH = os.path.expanduser("~/.bearblog_session")


def load_config():
    # 1. Config next to script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_config = os.path.join(script_dir, ".bearblog")

    # 2. Config in home directory
    if os.path.exists(local_config):
        config_path = local_config
    elif os.path.exists(CONFIG_PATH_HOME):
        config_path = CONFIG_PATH_HOME
    else:
        raise RuntimeError("Missing .bearblog config file in script directory or home directory")

    email = None
    password = None
    blog_name = None
    user_agent = None

    print (f"Reading config from {config_path}")
    with open(config_path, "r") as f:
        for line in f:
            if line.startswith("EMAIL="):
                email = line.split("=", 1)[1].strip()
            if line.startswith("PASSWORD="):
                password = line.split("=", 1)[1].strip()
            if line.startswith("BLOG_NAME="):
                blog_name = line.split("=", 1)[1].strip()
            if line.startswith("USER_AGENT="):
                user_agent = line.split("=", 1)[1].strip()

    if not email or not password or not blog_name:
        raise RuntimeError("Config missing EMAIL, PASSWORD, or BLOG_NAME")
    if not user_agent:
        raise RuntimeError("Config missing USER_AGENT")

    blog_url = f"https://bearblog.dev/{blog_name}"
    return email, password, blog_url, user_agent


def extract_csrf(html):
    soup = BeautifulSoup(html, "html.parser")
    token = soup.find("input", {"name": "csrf_token"})
    return token["value"] if token else None


def get_session():
    session = requests.Session()
    email, password, blog_url, user_agent = load_config()

    # Load existing cookie
    if os.path.exists(SESSION_PATH):
        with open(SESSION_PATH, "r") as f:
            cookie = f.read().strip()
            session.cookies.set("session", cookie, domain="bearblog.dev")

    session.headers.update({
        "User-Agent": user_agent,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "max-age=0",
        "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    })

    # Test if session is already valid
    r = session.get(f"{blog_url}/dashboard/posts")

    if "Log in" not in r.text and "Sign In" not in r.text:
        if "challenge-error-text" in r.text:
            print(f"Exception: Blocked by CloudFlare\nResponse: {r.text[:100]}\n\n")
            raise Exception("Blocked by CloudFlare")
        return session

    # Correct login URL
    login_url = "https://bearblog.dev/accounts/login/"

    # Load login page
    login_page = session.get(login_url)
    soup = BeautifulSoup(login_page.text, "html.parser")

    # Extract Django CSRF token
    csrf_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
    if not csrf_input:
        raise RuntimeError("Could not find CSRF token on login page")
    csrf = csrf_input["value"]

    # Correct payload based on your form
    payload = {
        "login": email,                     # correct field name
        "password": password,               # correct field name
        "remember": "on",                   # optional
        "csrfmiddlewaretoken": csrf         # correct CSRF field
    }

    headers = {
        "Referer": login_url                # Django requires this
    }

    # Submit login
    r = session.post(login_url, data=payload, headers=headers)

    # Correct success check
    if "Sign out" not in r.text and "Log out" not in r.text:
        print(r.text)
        raise RuntimeError("Login failed")

    # Save cookie
    cookie = session.cookies.get("session", domain="bearblog.dev")
    if cookie:
        with open(SESSION_PATH, "w") as f:
            f.write(cookie)

    return session


# -----------------------------
# LIST POSTS
# -----------------------------
def cmd_list(args):
    session = get_session()
    email, password, blog_url, user_agent = load_config()

    r = session.get(f"{blog_url}/dashboard/posts")
    soup = BeautifulSoup(r.text, "html.parser")

    posts = []

    for li in soup.select("ul.post-list li"):
        a = li.find("a")
        if not a:
            continue

        href = a.get("href", "").strip()
        title = a.text.strip()

        # Extract post ID from URL
        parts = href.strip("/").split("/")
        post_id = parts[-1]

        posts.append({
            "id": post_id,
            "title": title,
            "href": href
        })

    print(json.dumps(posts, indent=2))


def load_post(filepath):
    from datetime import datetime
    from pathlib import Path
    p = Path(filepath)
    raw = p.read_text(encoding="utf-8")

    if not raw.startswith("---"):
        print(f"Error: '{p.name}' has no opening '---' frontmatter delimiter.")
        print("File must begin with '---' followed by YAML fields (title, meta_description, etc.).")
        sys.exit(1)

    post = frontmatter.loads(raw)
    metadata = dict(post.metadata)
    content = post.content

    if not metadata.get("title"):
        metadata["title"] = p.stem.replace("-", " ").replace("_", " ").title()

    if isinstance(metadata.get("published_date"), datetime):
        metadata["published_date"] = metadata["published_date"].strftime("%Y-%m-%dT%H:%M:%S+00:00")

    if isinstance(metadata.get("tags"), list):
        metadata["tags"] = ", ".join(metadata["tags"])

    return metadata, content


def build_header_content(metadata):
    fields = ["title", "meta_description", "published_date", "tags"]
    lines = []
    for field in fields:
        value = metadata.get(field, "")
        lines.append(f"{field}:{value}")
    return "\r\n".join(lines)


# -----------------------------
# NEW POST
# -----------------------------
def cmd_new(args):
    session = get_session()
    email, password, blog_url, user_agent = load_config()

    metadata, body_content = load_post(args.file)
    header_content = build_header_content(metadata)

    # --- Load the "new post" page to get CSRF ---
    new_post_url = f"{blog_url}/dashboard/posts/new/"
    r = session.get(new_post_url)
    soup = BeautifulSoup(r.text, "html.parser")

    csrf_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
    if not csrf_input:
        print(r.text)
        raise RuntimeError("Could not find CSRF token on new post page")

    csrf = csrf_input["value"]

    # --- Build correct payload ---
    payload = {
        "csrfmiddlewaretoken": csrf,
        "publish": "true",
        "header_content": header_content,
        "body_content": body_content
    }

    # --- Submit the form (POST /new/) ---
    r = session.post(new_post_url, data=payload, allow_redirects=False)

    # --- Extract post ID from redirect ---
    if "Location" not in r.headers:
        print(r.text)
        raise RuntimeError("Post creation failed (no redirect)")

    redirect_url = r.headers["Location"].rstrip("/") + "/"
    post_id = redirect_url.split("/")[-2]

    # --- Output JSON ---
    print(json.dumps({
        "status": "created",
        "id": post_id,
        "published": True
    }, indent=2))


# -----------------------------
# UPDATE POST
# -----------------------------
def cmd_update(args):
    session = get_session()
    email, password, blog_url, user_agent = load_config()

    # Read new body content
    with open(args.file, "r", encoding="utf-8") as f:
        body_content = f.read().lstrip("\ufeff")

    edit_url = f"{blog_url}/dashboard/posts/{args.id}/"

    # Load edit page
    r = session.get(edit_url)
    soup = BeautifulSoup(r.text, "html.parser")

    # Extract CSRF
    csrf_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
    csrf = csrf_input["value"]

    # Extract header_content EXACTLY like browser innerText
    header_div = soup.find("div", {"id": "header_content"})
    header_content = extract_header_content(header_div)

    # Build payload
    payload = {
        "csrfmiddlewaretoken": csrf,
        "header_content": header_content,
        "body_content": body_content,
    }

    headers = {"Referer": edit_url}

    # POST update
    r = session.post(edit_url, data=payload, headers=headers)

    if r.status_code != 200:
        print(r.text)
        raise RuntimeError(f"Update failed (status {r.status_code})")

    print(json.dumps({"status": "updated", "id": args.id}, indent=2))


# -----------------------------
# DELETE POST
# -----------------------------
def cmd_delete(args):
    session = get_session()
    email, password, blog_url, user_agent = load_config()

    post_id = args.id

    delete_url = f"{blog_url}/dashboard/posts/{post_id}/delete/"

    # CSRF token comes from the cookie, not the page
    if "csrftoken" not in session.cookies:
        raise RuntimeError("No CSRF token in session cookies")

    csrf = session.cookies["csrftoken"]

    payload = {
        "csrfmiddlewaretoken": csrf
    }

    # Must include Referer header or Django will reject it
    headers = {
        "Referer": f"{blog_url}/dashboard/posts/{post_id}/"
    }

    r = session.post(delete_url, data=payload, headers=headers, allow_redirects=False)

    if r.status_code != 302:
        print(r.text)
        raise RuntimeError(f"Delete failed (status {r.status_code})")

    print(json.dumps({
        "status": "deleted",
        "id": post_id
    }, indent=2))


# -----------------------------
# PUBLISH POST
# -----------------------------
def cmd_publish(args):
    session = get_session()
    email, password, blog_url, user_agent = load_config()

    post_id = args.id
    edit_url = f"{blog_url}/dashboard/posts/{post_id}/"

    # CSRF from cookie
    if "csrftoken" not in session.cookies:
        raise RuntimeError("No CSRF token in session cookies")

    csrf = session.cookies["csrftoken"]

    payload = {
        "csrfmiddlewaretoken": csrf,
        "published": "true"
    }

    headers = {
        "Referer": edit_url
    }

    r = session.post(edit_url, data=payload, headers=headers)

    if r.status_code != 200:
        print(r.text)
        raise RuntimeError(f"Publish failed (status {r.status_code})")

    print(json.dumps({
        "status": "published",
        "id": post_id
    }, indent=2))


# def extract_clean_header(div):
#     # Convert <br> to newlines
#     for br in div.find_all("br"):
#         br.replace_with("\n")

#     # Convert <b>title:</b> to "title:"
#     for b in div.find_all("b"):
#         b.replace_with(b.get_text())

#     # Get raw text
#     text = div.get_text("\n")

#     # Normalize whitespace
#     lines = [line.strip() for line in text.split("\n") if line.strip()]

#     # Rejoin into clean header block
#     return "\n".join(lines)


def normalize_header_block(raw):
    lines = []
    key = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # If line ends with ":" it's a key
        if stripped.endswith(":"):
            key = stripped[:-1]  # remove colon
            continue

        # Otherwise it's a value
        if key:
            lines.append(f"{key}: {stripped}")
            key = None

    return "\n".join(lines)


def extract_header_content(header_div):
    # Clone the div so we can modify it safely
    div = header_div

    # Replace <br> with newline
    for br in div.find_all("br"):
        br.replace_with("\n")

    # Replace <b>...</b> with just the text inside
    for b in div.find_all("b"):
        b.replace_with(b.get_text())

    # Replace <span>...</span> with its text
    for span in div.find_all("span"):
        span.replace_with(span.get_text())

    # Now get the text exactly like innerText
    text = div.get_text()

    # Normalize whitespace like the browser does
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    return "\n".join(lines)


def cmd_load(args):
    session = get_session()
    email, password, blog_url, user_agent = load_config()

    edit_url = f"{blog_url}/dashboard/posts/{args.id}/"
    r = session.get(edit_url)
    soup = BeautifulSoup(r.text, "html.parser")

    # Extract header
    header_div = soup.find("div", {"id": "header_content"})
    if not header_div:
        raise RuntimeError("Could not find header_content DIV")

    # Convert <br> to newlines
    for br in header_div.find_all("br"):
        br.replace_with("\n")

    # Extract text
    header_text = header_div.get_text("\n").strip()

    # Extract body
    body_textarea = soup.find("textarea", {"name": "body_content"})
    if not body_textarea:
        raise RuntimeError("Could not find body_content textarea")

    body_text = body_textarea.text.lstrip("\ufeff")

    print("\n=== HEADER ===\n")
    print(header_text)
    print("\n=== BODY ===\n")
    print(body_text)


# -----------------------------
# MAIN
# -----------------------------
def main():
    parser = argparse.ArgumentParser(
        description=(
            "BearBlog CLI (Free Plan)\n\n"
            "Examples:\n"
            "  bearcli list\n"
            "  bearcli new \"My Post Title\" --file post.md\n"
            "  bearcli load abc123xyz\n"
            "  bearcli delete abc123xyz\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser(
        "list",
        help="List all posts in your Bear Blog dashboard."
    )
    p_list.set_defaults(func=cmd_list)

    # new
    p_new = sub.add_parser(
        "new",
        help="Create a new post from a markdown file."
    )
    p_new.add_argument("--file", required=True, help="Path to the markdown file (must have YAML frontmatter).")
    p_new.set_defaults(func=cmd_new)

    # load
    p_load = sub.add_parser(
        "load",
        help="Load a post by ID and print its header + body."
    )
    p_load.add_argument("id", help="Post ID to load.")
    p_load.set_defaults(func=cmd_load)

    # delete
    p_delete = sub.add_parser(
        "delete",
        help="Delete a post by ID."
    )
    p_delete.add_argument("id", help="Post ID to delete.")
    p_delete.set_defaults(func=cmd_delete)

    # Disabled commands (kept for future use)
    p_update = sub.add_parser("update", help="Update a post by ID.")
    p_update.add_argument("id")
    p_update.add_argument("--file", required=True)
    p_update.set_defaults(func=cmd_update)

    p_publish = sub.add_parser("publish", help="Publish a post by ID.")
    p_publish.add_argument("id")
    p_publish.set_defaults(func=cmd_publish)

    # p_unpublish = sub.add_parser("unpublish", help="Unpublish a post (disabled).")
    # p_unpublish.add_argument("id")
    # p_unpublish.set_defaults(func=cmd_unpublish)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
