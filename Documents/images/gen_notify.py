"""通知基盤の図 。core の notify-queue → notifier → SES / Slack と、失敗時の DLQ・アラームを描く。

実行: python gen_notify.py
出力: notify-flow.png (このファイルと同じ場所)
"""

import os

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.database import Dynamodb
from diagrams.aws.engagement import SimpleEmailServiceSes
from diagrams.aws.integration import SimpleNotificationServiceSns, SimpleQueueServiceSqs
from diagrams.aws.management import Cloudwatch, SystemsManagerParameterStore
from diagrams.aws.storage import S3
from diagrams.onprem.client import Users
from diagrams.saas.chat import Slack

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "notify-flow")
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
    "label": "通知基盤 (service-platform.notify.v1)",
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

red = {"color": "#C62828", "fontcolor": "#C62828"}
brown = {"color": "#8D6E63", "fontcolor": "#6D4C41"}


def cluster(name, color):
    return Cluster(
        name,
        graph_attr={"fontname": FONT, "fontsize": "22", "bgcolor": color, "margin": "18"},
    )


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
    with cluster("apps/{app}", "#E8F5E9"):
        app_fn = Lambda("サービスの\nLambda\nnotify.send()")

    with cluster("core (service-platform-core-{env})", "#FFF8E1"):
        queue = SimpleQueueServiceSqs("service-platform-\nnotify-queue\n-{env}")
        notifier = Lambda("service-platform-\ncore-notifier\n-{env}")
        dlq = SimpleQueueServiceSqs("service-platform-\nnotify-dlq\n-{env}")
        alarm = Cloudwatch("DLQ の\nアラーム")
        ops = SimpleNotificationServiceSns("service-platform-\nops-{env}")
        ses = SimpleEmailServiceSes("SES v2\nSendEmail")
        idem = Dynamodb("冪等性\nテーブル")
        attachments = S3("添付\nバケット\n{app}/...")

    ssm = SystemsManagerParameterStore("SSM\nshared/\nslack/*")
    slack = Slack("Slack")
    mail = Users("メールの\n宛先")
    admin = Users("基盤担当")

    app_fn >> Edge(label="put_attachment", **brown) >> attachments
    app_fn >> Edge(label="SendMessage", **red) >> queue
    queue >> notifier
    notifier >> Edge(label="冪等", style="dashed") >> idem
    notifier >> Edge(label="添付を読む", style="dashed", **brown) >> attachments
    notifier >> Edge(label="email") >> ses
    ses >> mail
    notifier >> Edge(label="slack") >> slack
    notifier >> Edge(label="秘密", style="dotted") >> ssm
    queue >> Edge(label="3 回失敗", style="dashed", **red) >> dlq
    dlq >> alarm >> ops
    ops >> Edge(label="メール") >> admin
    ses >> Edge(label="バウンスと苦情", style="dotted") >> ops
