$(function() {

    var cam_key = $("#cam_key"),
        cam_name = $("#cam_name"),
        cam_url = $("#cam_url"),
        cam_enabled = $("#cam_enabled"),
        cam_poll_max_fps = $("#cam_poll_max_fps"),
        cam_alert_max_fps = $("#cam_alert_max_fps"),
        cam_num_secs_after = $("#cam_num_secs_after");

    var allFields = $([]).add(cam_name).add(cam_url).add(cam_poll_max_fps).add(cam_alert_max_fps).add(cam_num_secs_after);
    var tips = $(".validateTips");

    function updateTips(t) {
        tips
            .text(t)
            .addClass('ui-state-highlight');
        setTimeout(function() {
            tips.removeClass('ui-state-highlight', 1500);
        }, 500);
    }

    function checkLength(o,n,min,max) {

        if ( o.val().length > max || o.val().length < min ) {
            // display the error message in the tip area.
            o.addClass('ui-state-error');
            updateTips("Length of " + n + " must be between "+min+" and "+max+".");

            // focus the tab containing the offending input element.
            $("#dialog-tabs").tabs("select", o.closest('.ui-tabs-panel').attr('id'));
            return false;
        } else {
            return true;
        }

    }

    function checkRegexp(o,regexp,n) {

        if ( !( regexp.test( o.val() ) ) ) {
            // display the error message in the tip area.
            o.addClass('ui-state-error');
            updateTips(n);

            // focus the tab containing the offending input element.
            $("#dialog-tabs").tabs("select", o.closest('.ui-tabs-panel').attr('id'));
            return false;
        } else {
            return true;
        }

    }

    $(document).ajaxError(function(e, xhr, settings, exception) {
        alert('error in: ' + settings.url + ' \n'+'error:\n' + xhr.responseText );
        // TODO: re-enable disabled button?
    }); 
    
    // initialize the tabs.
    $("#dialog-tabs").tabs();

    // initialize the dialog box and install the event handlers.
    $("#dialog-form").dialog({
        autoOpen: false,
        height: 400,
        width: 550,
        modal: true,
        buttons: {
            'Save': function() {
                var bValid = true;
                allFields.removeClass('ui-state-error');

                bValid = bValid && checkLength(cam_name,"name",3,100);
                //bValid = bValid && checkRegexp(cam_url,/^https?:\/\/([-\w\.]+)+(:\d+)?(\/([\w/_\.]*(\?\S+)?)?)?$/,"Source URL is invalid.");
                bValid = bValid && checkRegexp(cam_url,/^https?:\/\/\S+$/,"Source URL is invalid.");
                bValid = bValid && checkRegexp(cam_poll_max_fps,/^[0-9]+$/,"Max frame rate (normal) must be an integer.");
                bValid = bValid && checkRegexp(cam_alert_max_fps,/^[1-9][0-9]*$/,"Max frame rate (alert) must be an integer.");
                bValid = bValid && checkRegexp(cam_num_secs_after,/^[1-9][0-9]*(\.[0-9]+)?$/,"Number of seconds after alert must be a decimal.");                    
                    
                if (bValid) {
                    $(this).dialog("disable");

                    if (cam_key.val() != '') {
                        // Editing existing camera.
                        $.post("/camera/edit", 
                                {   camera: cam_key.val(),
                                    cmd: 'save',
                                    name: cam_name.val(),
                                    url: cam_url.val(),
                                    enabled: (cam_enabled.attr('checked') ? 1 : 0),
                                    poll_max_fps: cam_poll_max_fps.val(),
                                    alert_max_fps: cam_alert_max_fps.val(),
                                    num_secs_after: cam_num_secs_after.val() },
                                function(data) {
                                    alert(data);
                                    location.reload();
                                });


                    } else {
                        // Adding a new camera.
                        $.post("/camera/add", 
                                {   name: cam_name.val(),
                                    url: cam_url.val(),
                                    enabled: (cam_enabled.attr('checked') ? 1 : 0),
                                    poll_max_fps: cam_poll_max_fps.val(),
                                    alert_max_fps: cam_alert_max_fps.val(),
                                    num_secs_after: cam_num_secs_after.val() },
                                function(data) {
                                    alert(data);
                                    location.reload();
                                });
                     }

                }
            },
            'Cancel': function() {
                $(this).dialog('close');
            }
        },
        close: function() {
            allFields.val('').removeClass('ui-state-error');
        }
    });
        
    // install the handler that opens the "add" dialog.
    $('#add_camera_button')
        .click(function() {            
            // wipe the tip area.
            tips.text('\u00A0');     // nbsp

            // focus the first tab.
            $("#dialog-tabs").tabs("select", 0);

            // wipe the hidden input, to ensure a new record is created.
            cam_key.val('');

            // run the dialog.
            $('#dialog-form')
                .dialog('option', 'title', 'Add new camera source')
                .dialog("enable")
                .dialog('open');
        });

    // initialize the dialog and install the event handlers.
    $("#dialog-confirm").dialog({
        autoOpen: false,
        resizable: false,
        height:240,
        width: 300,
        modal: true,
        buttons: {
            'Delete': function() {
                $(this).dialog("disable");
                // TODO: show spinner

                $.post("/camera/delete", 
                        { camera: cam_key.val() },
                        function(data) {
                            alert(data);
                            location.reload();
                        });
            },
            'Cancel': function() {
                $(this).dialog('close');
            }
        }
    });



});

function editCameraButton(camkey) {
    // wipe the tip area.
    $(".validateTips").text('\u00A0');     // nbsp

    // focus the first tab.
    $("#dialog-tabs").tabs("select", 0);

    // update the hidden element to store the object being edited.
    $('#cam_key').val(camkey);

    // fetch the current object values then run the dialog.
    $.post("/camera/edit", 
            { camera: camkey, cmd: 'get' },
            function(data) {
                if ($('#cam_key').val() != data.key) {
                    alert("Unexpected server response.");
                    return;
                }

                // update the form to show the current object values.
                $('#cam_name').val(data.name);
                $('#cam_url').val(data.url);
                $('#cam_enabled').attr('checked', data.enabled != 0);
                $('#cam_poll_max_fps').val(data.poll_max_fps);
                $('#cam_alert_max_fps').val(data.alert_max_fps);
                $('#cam_num_secs_after').val(data.num_secs_after);

                // run the dialog.
                $("#dialog-form")
                    .dialog('option', 'title', 'Edit camera source')
                    .dialog("enable")
                    .dialog('open');
            }, 'json');
}


function deleteCameraButton(camkey) {
    $('#cam_key').val(camkey);
    $("#dialog-confirm").dialog("open");
}

function triggerCameraButton(camkey) {
    $.get('/camera/trigger?camera=' + camkey, function(data) { alert(data); });
}