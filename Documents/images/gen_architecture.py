"""土台の構成図 。ADR-001 の採用後の形 (WAF・台帳・監査テーブル・/camera/ を含む) を描く。

実行: python gen_architecture.py
出力: architecture.png (このファイルと同じ場所)
"""

import os

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.database import Dynamodb
from diagrams.aws.engagement import SimpleEmailServiceSes
from diagrams.aws.integration import SimpleQueueServiceSqs
from diagrams.aws.management import SystemsManagerParameterStore
from diagrams.aws.network import APIGateway, CloudFront
from diagrams.aws.security import WAF, Cognito
from diagrams.aws.storage import S3
from diagrams.onprem.client import Users
from diagrams.saas.chat import Slack

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "architecture")
FONT = "Noto Sans CJK JP"

Cluster._default_graph_attrs.update({"fontname": FONT, "fontsize": "22"})

graph_attr = {
    "fontsize": "32",
    "fontname": FONT,
    "bgcolor": "white",
    "pad": "0.4",
    "dpi": "200",
    "ranksep": "0.8",
    "nodesep": "0.45",
    "splines": "spline",
    "newrank": "true",
    "labelloc": "t",
    "label": "SERVICE-PLATFORM 土台の構成 (ADR-001 採用後の形)",
}
node_attr = {
    "fontsize": "18",
    "fontname": FONT,
    "width": "1.6",
    "height": "1.6",
    "fixedsize": "true",
    "imagepos": "tc",
    "labelloc": "b",
}
edge_attr = {"fontsize": "16", "fontname": FONT, "color": "#555555"}

dotted = {"style": "dotted", "color": "#999999", "fontcolor": "#666666"}


def cluster(name, color, dashed_border=False):
    attr = {"fontname": FONT, "fontsize": "22", "bgcolor": color, "margin": "18"}
    if dashed_border:
        attr.update({"style": "rounded,dashed", "pencolor": "#999999"})
    return Cluster(name, graph_attr=attr)


def colored(text, color):
    return Edge(label=text, color=color, fontcolor=color)


with Diagram(
    "",
    filename=OUTPUT,
    outformat="png",
    direction="LR",
    show=False,
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    with cluster("us-east-1 (CloudFront 用)", "#F2F2F2", dashed_border=True):
        waf_cf = WAF("WAF\nweb ACL\nCloudFront 用")

    user = Users("利用者の\nブラウザ\nお客様・社員\n・admin")

    with cluster("edge (service-platform-edge-{env})", "#E3F2FD"):
        cf = CloudFront("CloudFront\nSPA と API\nを同居")
        spa = S3("SPA バケット\n直下\n土台の SPA")
        cam_spa = S3("SPA バケット\ncamera/\nサービス例\nSPA")

    with cluster("apps/{app} (service-platform-app-{app}-{env})", "#E8F5E9"):
        api = APIGateway("HTTP API\nJWT\nauthorizer")
        fn = Lambda("Lambda\n+ shared\nレイヤ")
        svc_data = Dynamodb("サービスの\nテーブル\nと S3")

    with cluster("core (service-platform-core-{env})", "#FFF8E1"):
        cognito = Cognito("Cognito\nユーザー\nプール")
        waf_cg = WAF("WAF\nweb ACL\nユーザー\nプール用")
        ledger = Dynamodb("台帳\n利用者・\n割り当て・\n許可")
        audit = Dynamodb("監査\nテーブル\n追記だけ")
        queue = SimpleQueueServiceSqs("notify-queue\n+ DLQ")
        notifier = Lambda("notifier")
        ssm = SystemsManagerParameterStore("SSM\n受け渡し\n/service-platform/\n{env}/")
        ses = SimpleEmailServiceSes("SES v2\nメール")

    slack = Slack("Slack")

    user >> Edge(label="HTTPS") >> cf
    user >> Edge(label="サインイン", color="#2E7D32", fontcolor="#2E7D32") >> cognito
    waf_cf >> Edge(**dotted) >> cf
    cf >> Edge(label="/*") >> spa
    cf >> Edge(label="/camera/*") >> cam_spa
    cf >> Edge(label="/api/{app}/*", color="#1565C0", fontcolor="#1565C0") >> api
    api >> Edge(label="JWT 検証", style="dashed", color="#2E7D32", fontcolor="#2E7D32") >> cognito
    api >> fn
    fn >> svc_data
    fn >> colored("読む", "#00838F") >> ledger
    fn >> colored("追記", "#6A1B9A") >> audit
    fn >> colored("notify.send", "#C62828") >> queue
    queue >> notifier
    notifier >> ses
    notifier >> slack
    waf_cg >> Edge(label="ChangePassword\nを Block", **dotted) >> cognito
    ssm >> Edge(style="invis") >> api
