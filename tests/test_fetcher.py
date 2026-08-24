from src import fetcher


class _FakeResponse:
    status_code = 200
    url = "https://martinezfcc.com/"
    headers = {"Content-Type": "text/html; charset=utf-8"}
    text = """
    <!doctype html>
    <html>
      <head>
        <title>Growing Minds Academy</title>
        <script>
          window['__canva_public_path__'] = '_assets/';
          window['bootstrap'] = JSON.parse('{
            "page": {
              "A": [
                {"a":{"A":[{"A?":"A","A":"Provider\\\\n Ms. Jasmine\\\\n"}]}},
                {"a":{"A":[{"A?":"A","A":"childrenfirsthappyhearts@gmail.com\\\\n"}]}}
              ]
            }
          }');
        </script>
      </head>
      <body><div id="root"></div></body>
    </html>
    """


class _StylizedResponse:
    status_code = 200
    url = "https://www.thelittlecenter.com/about-us"
    headers = {"Content-Type": "text/html; charset=utf-8"}
    text = """
    <!doctype html>
    <html>
      <body>
        <a href="/about-us">𝔸𝕓𝕠𝕦𝕥 𝕌𝕤</a>
        <h1>𝕆𝕨𝕟𝕖𝕣 & ℙ𝕣𝕠𝕘𝕣𝕒𝕞 𝔻𝕚𝕣𝕖𝕔𝕥𝕠𝕣</h1>
        <p>𝙷𝚒, 𝙸’𝚖 𝙴𝚕𝚒𝚊𝚗𝚊, 𝚏𝚘𝚞𝚗𝚍𝚎𝚛 𝚘𝚏 𝚃𝚑𝚎 𝙻𝚒𝚝𝚝𝚕𝚎 𝙲𝚎𝚗𝚝𝚎𝚛, 𝙻𝙻𝙲.</p>
        <p>𝕥𝕙𝕖𝕝𝕚𝕥𝕥𝕝𝕖𝕔𝕖𝕟𝕥𝕖𝕣𝟟𝟟@𝕘𝕞𝕒𝕚𝕝.𝕔𝕠𝕞</p>
      </body>
    </html>
    """


def test_fetch_extracts_canva_bootstrap_text(monkeypatch):
    def fake_get(*_args, **_kwargs):
        return _FakeResponse()

    monkeypatch.setattr(fetcher.requests, "get", fake_get)

    page = fetcher.fetch("https://martinezfcc.com/")

    assert "Provider Ms. Jasmine" in page.text
    assert "childrenfirsthappyhearts@gmail.com" in page.text


def test_fetch_normalizes_stylized_unicode_text(monkeypatch):
    def fake_get(*_args, **_kwargs):
        return _StylizedResponse()

    monkeypatch.setattr(fetcher.requests, "get", fake_get)

    page = fetcher.fetch("https://www.thelittlecenter.com/about-us")

    assert "Owner & Program Director" in page.text
    assert "Hi, I’m Eliana, founder of The Little Center, LLC." in page.text
    assert "thelittlecenter77@gmail.com" in page.text
    assert page.outbound_links[0]["text"] == "About Us"
