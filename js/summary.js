$(function() {
		
	var cam_key = $("#cam_key"),
        cam_name = $("#cam_name"),
        cam_url = $("#cam_url"),
        cam_enabled = $("#cam_enabled"),
        cam_poll_max_fps = $("#cam_poll_max_fps"),
        cam_alert_max_fps = $("#cam_alert_max_fps"),
        cam_num_secs_after = $("#cam_num_secs_after");

    var allFields = $([]).add(cam_key).add(cam_name).add(cam_url).add(cam_poll_max_fps).add(cam_alert_max_fps).add(cam_num_secs_after);
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
			o.addClass('ui-state-error');
			updateTips("Length of " + n + " must be between "+min+" and "+max+".");
			return false;
		} else {
			return true;
		}

	}

	function checkRegexp(o,regexp,n) {

		if ( !( regexp.test( o.val() ) ) ) {
			o.addClass('ui-state-error');
			updateTips(n);
			return false;
		} else {
			return true;
		}

	}

    $(document).ajaxError(function(e, xhr, settings, exception) {
        alert('error in: ' + settings.url + ' \n'+'error:\n' + xhr.responseText );
        // TODO: re-enable disabled button?
    }); 
		
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
                bValid = bValid && checkRegexp(cam_url,/^https?:\/\/([-\w\.]+)+(:\d+)?(\/([\w/_\.]*(\?\S+)?)?)?$/,"Source URL is invalid.");
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
                                    enabled: cam_enabled.val(),
                                    poll_max_fps: cam_poll_max_fps.val(),
                                    alert_max_fps: cam_alert_max_fps.val(),
                                    num_secs_after: cam_num_secs_after.val() },
                                function(data) {
                                    //$('.result').html(data);
                                    alert(data);
                                    // TODO: force page refresh.
                                });


                    } else {
                        // Adding a new camera.
                        $.post("/camera/add", 
                                {   name: cam_name.val(),
                                    url: cam_url.val(),
                                    enabled: cam_enabled.val(),
                                    poll_max_fps: cam_poll_max_fps.val(),
                                    alert_max_fps: cam_alert_max_fps.val(),
                                    num_secs_after: cam_num_secs_after.val() },
                                function(data) {
                                    //$('.result').html(data);
                                    alert(data);
                                    // TODO: force page refresh
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
		
		
		
	$('#add_camera_button')
		.click(function() {
            cam_key.val('');
			$('#dialog-form')
                .dialog('option', 'title', 'Add new camera source')
                .dialog("enable")
                .dialog('open');
		});
/*
    function editCameraButton(camkey) {
        $.post("/camera/edit", 
                { camera: camkey,
                  cmd: 'get' },
                function(data) {
                    //alert(data);
                    cam_key.val("xxxx");
                    cam_name.val("xxxx");
                    cam_url.val("xxxx");
                    cam_enabled.val("xxxx");
                    cam_poll_max_fps.val("xxxx");
                    cam_alert_max_fps.val("xxxx");
                    cam_num_secs_after.val("xxxx");

                    $("#dialog-form")
                        .dialog('option', 'title', 'Edit camera source')
                        .dialog("enable")
                        .dialog('open');
                }, 'json');
    }

    $("#dialog-confirm").dialog({
        autoOpen: false,
		resizable: false,
		height:140,
		modal: true,
        buttons: {
			'Delete': function() {
                $(this).dialog("disable");
                // TODO: show spinner

                $.post("/camera/delete", 
                        { camera: cam_key.val() },
                        function(data) {
                            //alert(data);
                            $(this).dialog('close');
                            // TODO: refresh page
                        });
			},
			'Cancel': function() {
				$(this).dialog('close');
			}
		}
    });
*/



});


    function deleteCameraButton(camkey) {
        alert("Moo");
        //cam_key.val(camkey);
        //$("#dialog-confirm").dialog("open");
    }

