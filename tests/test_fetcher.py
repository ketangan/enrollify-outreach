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


def test_fetch_extracts_canva_bootstrap_text(monkeypatch):
    def fake_get(*_args, **_kwargs):
        return _FakeResponse()

    monkeypatch.setattr(fetcher.requests, "get", fake_get)

    page = fetcher.fetch("https://martinezfcc.com/")

    assert "Provider Ms. Jasmine" in page.text
    assert "childrenfirsthappyhearts@gmail.com" in page.text
