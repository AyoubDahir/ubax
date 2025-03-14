// custom_title.js

import { registry } from "@web/core/registry";
import { WebClient } from "@web/webclient/webclient";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";

patch(WebClient.prototype, {
    setup() {
        this._super();
        const title = useService("title");
        title.setParts({ zopenerp: "My Title" });
    },
});
